# Created: 2026-05-22
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-D;
#   /Users/leofitz/.claude/jobs/866db2ea/P0_FOLLOWUP_BUNDLE_LAYER_SPEC.md §3, §4;
#   docs/operations/task_2026-05-23_probability_phantom_edge/FIX_PLAN.md §3 §4 (LIVE-PROB-P0);
#   /Users/leofitz/.claude/jobs/866db2ea/IMPL_SPEC_operator.md §B §E §F (2026-05-23 operator spec)
"""Probability sanity validator for HIGH distribution outputs.

Gates:
  - non-finite p_raw / p_cal              → "non_finite_probability"
  - sum(p_raw) not 1 (±1e-6)             → "P_RAW_NOT_CATEGORICAL:{sum}"
  - sum(p_cal) not 1 (±1e-3)             → "P_CAL_NOT_CATEGORICAL:{sum}"
  - point bucket (low≈high) is mode,
    p_cal[mode]>0.5, member support<0.25  → "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:p={},support={}"
  - market px<0.05 AND p_cal>0.35         → "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:idx={},price={},p={}"
  - else                                  → (True, None)

check_cumulative_tail_discrepancy (Gate 6 / LIVE-PROB-P0, TELEMETRY ONLY):
  - tail bins = underpriced quoted bins where 0 < p_market < low_price_threshold
  - computes family-level tail_cal/tail_mkt/entropy for audit columns; does NOT reject
  - called at evaluator.py ~4637 as telemetry; rejection moved to per-edge site

check_edge_bin_tail_discrepancy (Gate 6 / LIVE-PROB-P0, LEGACY REJECTION PREDICATE):
  - per-candidate-edge-bin check: edge_bin must be sub-floor, ratio >= K,
    and sit in a contiguous sub-floor run >= tail_min_bins on tail side of mode
  - SUPERSEDED by probability_edge_bin_sanity (see below); retained for backward compatibility
  - called in per-edge evaluation loop BEFORE economic floor             → "PROB_EDGE_BIN_TAIL_DISCREPANCY:..."

probability_edge_bin_sanity (Gate 6 / LIVE-PROB-P0, OPERATOR BINDING SPEC §B):
  - Full predicate per operator spec 2026-05-23.
  - CRITICAL SAFETY: settled_member_support >= min_edge_bin_member_support (0.05) → PASS
    even if ratio is high. Protects genuine BIMODAL edges.
  - settled_member_support = p_raw[selected_bin_idx] (fraction of MC-rounded members in bin)
  - Reads config from settings.json ``probability_edge_bin_sanity`` block (new key).
  - Returns (ok, reason_code, telemetry_dict) where telemetry_dict always populated.

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

# LIVE-PROB-P0 (Gate 6) defaults.
# K=3.0: Amsterdam ratio is 0.211/0.059 ≈ 3.58; K=3.0 catches with margin while
# a fair distribution with one sub-floor bin and well-calibrated p_cal (ratio ~1-2)
# will not trigger. K=4.0 would miss Amsterdam. K=2.5 would over-fire on mild tails.
# Externalized to settings.json::probability_sanity.{tail_discrepancy_k,
# tail_market_mass_floor, tail_min_bins} for operator tuning.
_DEFAULT_TAIL_DISCREPANCY_K = 3.0
# tail_min_bins: minimum number of sub-floor bins that must be present on a given
# side before the ratio check fires. Replay (2026-05-23) showed 64 FP candidates
# were all single-bin dust-bin detections (n_tail_bins=1, market_price ≈ 0.001).
# Requiring ≥2 contiguous sub-floor bins eliminates these while preserving the
# Amsterdam case (5 sub-floor left-tail bins). Set to 1 to restore old behavior.
_DEFAULT_TAIL_MIN_BINS = 2
# tail_market_mass_floor: gate only fires when total underpriced quoted mass is
# substantial enough to represent a real structural discrepancy (not a rounding
# artifact on a single near-boundary bin). 0.10 means ≥10% of market probability
# sits in sub-floor bins before the cumulative check activates.
# Amsterdam: 0.059 < 0.10 — below floor → gate fires.
# Fair distribution with 1 sub-floor bin at 0.04: 0.04 < 0.10 → gate fires ONLY
# if cumulative p_cal is also ≥3× that 0.04. Pairs with K to prevent single-bin FP.
# Wait — 0.059 < 0.10 means we DO check the ratio. If floor was 0.10 Amsterdam
# would trigger (0.059 < 0.10 = tail is below mass floor = second condition met).
# The second condition is sum(p_market[tail]) < tail_market_mass_floor — meaning
# the market is severely discounting this tail (market gives it little weight).
_DEFAULT_TAIL_MARKET_MASS_FLOOR = 0.10


def _sanity_thresholds() -> dict[str, float]:
    """Read probability_sanity thresholds from settings, defaulting to the
    current hardcoded values.  Defensive: a missing block or missing key falls
    back to the default, so the gate's behavior is unchanged when config is absent.
    """
    block: dict[str, Any] = {}
    from src.config import settings

    try:
        raw = settings["probability_sanity"]
    except (KeyError, TypeError):
        raw = None
    if isinstance(raw, dict):
        block = raw
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
        "tail_discrepancy_k": float(
            block.get("tail_discrepancy_k", _DEFAULT_TAIL_DISCREPANCY_K)
        ),
        "tail_market_mass_floor": float(
            block.get("tail_market_mass_floor", _DEFAULT_TAIL_MARKET_MASS_FLOOR)
        ),
        "tail_min_bins": int(
            block.get("tail_min_bins", _DEFAULT_TAIL_MIN_BINS)
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


def check_cumulative_tail_discrepancy(
    *,
    bins: Sequence,
    p_cal: np.ndarray,
    market_prices: np.ndarray | None,
) -> tuple[bool, str | None, dict | None]:
    """Gate 6 (LIVE-PROB-P0): symmetric cumulative tail-mass discrepancy check.

    Detects phantom edges where the model assigns substantial cumulative probability
    to a tail region that the market has uniformly underpriced. Unlike Gate 5
    (which checks per-bin p_cal > 0.35), this gate accumulates evidence across all
    underpriced bins on EITHER side of the mode — catching Amsterdam-style cases
    (left-tail) and warm-bias cases (right-tail, natural for LOW-metric markets or
    warm-biased ensembles) where no individual bin trips Gate 5 but the cumulative
    model probability far exceeds the market's assessment.

    Design intent (tail-bin definition):
      Tail bins = underpriced QUOTED bins where 0 < market_price < low_price_threshold.
      Unquoted bins (px == 0.0) are excluded per Gate 5 convention — unquoted means
      "no quote exists", not "market says impossible".
      Left tail = sub-floor bins at indices < mode_idx (argmax of p_cal).
      Right tail = sub-floor bins at indices > mode_idx.
      Sides are checked independently so a fair edge on one side cannot mask a
      phantom on the other.

    Rejection condition (checked PER SIDE, independently):
      sum(p_cal[tail_side]) >= K * sum(p_market[tail_side])
      AND sum(p_market[tail_side]) < tail_market_mass_floor

    The second condition (market tail mass below floor) ensures the gate only fires
    when the market genuinely discounts the tail. Without it, a well-spread
    distribution where market and model agree (both ~10% in tail) would spuriously
    reject.

    Amsterdam case (LEFT-tail phantom):
      left_tail bins = indices 0–4 (bins 19–23°C, all with 0 < p_market < 0.05)
      sum(p_cal[0:5]) = 0.211, sum(p_market[0:5]) = 0.059
      ratio = 3.58 >= K=3.0, AND 0.059 < tail_market_mass_floor=0.10 → REJECT

    RIGHT-tail phantom (mirror case):
      Warm-biased ensemble on a HIGH-metric market: mode at low-end bin, several
      right-side bins at p_market < 0.05 while p_cal assigns substantial mass.
      sum(p_cal[right_tail]) >= K × sum(p_market[right_tail]) → REJECT.

    Thresholds K and tail_market_mass_floor are read from settings.json
    ``probability_sanity`` block with defaults 3.0 and 0.10 respectively.
    Returns:
        (True, None, evidence_dict) if no tail discrepancy detected.
        (False, reason, evidence_dict) where reason = "PROB_DISTRIBUTION_TAIL_DISCREPANCY:..."
        evidence_dict always contains keys: tail_cal, tail_mkt, entropy
          (combined both sides; non-None whenever market_prices is not None).

    The caller (evaluator.py) reads ``settings["probability_sanity"]["tail_discrepancy_mode"]``
    ("hard"|"shadow") to decide whether to hard-reject or log-only when this returns (False,...).
    The predicate always returns the truthful (ok=False, reason) when a phantom is detected,
    regardless of the mode flag — making it independently testable.
    """
    if market_prices is None:
        return True, None, None

    p_cal = np.asarray(p_cal, dtype=np.float64)
    market_prices = np.asarray(market_prices, dtype=np.float64)
    thresholds = _sanity_thresholds()
    low_price_threshold = thresholds["low_price_threshold"]
    K = thresholds["tail_discrepancy_k"]
    mass_floor = thresholds["tail_market_mass_floor"]
    tail_min_bins = thresholds["tail_min_bins"]
    # Entropy over p_cal (nats): always computed for observability.
    p_pos = p_cal[p_cal > 0.0]
    entropy = float(-np.sum(p_pos * np.log(p_pos)))

    mode_idx = int(np.argmax(p_cal))
    sub_floor_mask = (market_prices > 0.0) & (market_prices < low_price_threshold)

    # Left tail: sub-floor bins strictly LEFT of mode.
    # Amsterdam: mode at bin 5 (24°C); left bins = 0..4 (19–23°C).
    left_mask = sub_floor_mask.copy()
    left_mask[mode_idx:] = False  # zero bin at mode_idx and right

    # Right tail: sub-floor bins strictly RIGHT of mode.
    # Mirror case: warm-bias ensemble; sub-floor high-end bins where p_cal >> p_market.
    right_mask = sub_floor_mask.copy()
    right_mask[: mode_idx + 1] = False  # zero bin at mode_idx and left

    # Aggregate for evidence (combined both sides).
    combined_cal = float(p_cal[sub_floor_mask].sum()) if sub_floor_mask.any() else 0.0
    combined_mkt = float(market_prices[sub_floor_mask].sum()) if sub_floor_mask.any() else 0.0
    evidence = {
        "tail_cal": combined_cal,
        "tail_mkt": combined_mkt,
        "entropy": entropy,
    }

    # Check each side independently; collect rejecting sides.
    reject_parts: list[str] = []
    for side_label, mask in (("left", left_mask), ("right", right_mask)):
        n = int(mask.sum())
        if n == 0:
            continue
        # Require ≥ tail_min_bins sub-floor bins on this side before ratio check.
        # Prevents single-bin dust-bin FPs (n=1, market_price ≈ 0.001). Replay
        # showed 64 such FPs at n=1 with tail_min_bins=1. Default is 2.
        if n < tail_min_bins:
            continue
        s_cal = float(p_cal[mask].sum())
        s_mkt = float(market_prices[mask].sum())
        if s_mkt <= 0.0:
            continue
        ratio = s_cal / s_mkt
        if ratio >= K and s_mkt < mass_floor:
            reject_parts.append(
                f"{side_label}:tail_cal={s_cal:.4f},tail_mkt={s_mkt:.4f},"
                f"ratio={ratio:.2f},n_tail_bins={n}"
            )

    if not reject_parts:
        return True, None, evidence

    reason = "PROB_DISTRIBUTION_TAIL_DISCREPANCY:" + "|".join(reject_parts)
    return False, reason, evidence


def check_edge_bin_tail_discrepancy(
    *,
    edge_bin_idx: int,
    p_cal: np.ndarray,
    market_prices: np.ndarray | None,
) -> tuple[bool, str | None]:
    """Per-edge-bin phantom detection (LIVE-PROB-P0 edge-level predicate).

    Rejects a specific candidate edge bin when ALL of the following hold:
      1. edge_bin_idx is strictly on the sub-floor side of the distribution mode
         (not at the mode itself, not on the well-priced side)
      2. 0 < market_prices[edge_bin_idx] < low_price_threshold
         (unquoted bins — px==0 — are excluded per Gate 5 / Gate 6 convention)
      3. p_cal[edge_bin_idx] / market_prices[edge_bin_idx] >= K
         (per-bin ratio: model assigns far more probability than market implies)
      4. edge_bin_idx sits inside a contiguous run of sub-floor-quoted bins on its
         side of the mode of length >= tail_min_bins.
         Contiguous = consecutive indices where 0 < p_mkt < low_price_threshold.
         The run is bounded by the mode (mode breaks contiguity even if
         mode's p_mkt is also sub-floor), unquoted bins (p_mkt==0), and
         well-priced bins (p_mkt >= low_price_threshold).

    Design rationale:
      Family-level check fires on Jeddah/Tokyo FPs because those candidates
      have 2-3 incidental sub-floor left-tail bins even though the ACTUAL EDGE
      is on a well-priced bin (p_mkt=0.13 or p_mkt=0.10).  Moving the check to
      the edge bin itself makes the gate conditional on "is THIS candidate bin
      itself a phantom?" — naturally passing Jeddah/Tokyo while still catching
      Amsterdam (edge_bin_idx=3, 4 contiguous left-tail bins 0-3 all sub-floor,
      p_mkt[3]=0.047, p_cal[3]/p_mkt[3]=3.99 >= 3.0).

    Args:
        edge_bin_idx: index of the candidate edge bin (edge.support_index).
        p_cal: calibrated probability array, shape (n_bins,).
        market_prices: per-bin market prices, shape (n_bins,).  None → always PASS.

    Returns:
        (True, None)          — edge bin is not a phantom; safe to proceed.
        (False, reason_str)   — edge bin is a phantom; reason includes detail.
    """
    if market_prices is None:
        return True, None

    p_cal_arr = np.asarray(p_cal, dtype=np.float64)
    mkt_arr = np.asarray(market_prices, dtype=np.float64)
    n = len(p_cal_arr)

    if edge_bin_idx < 0 or edge_bin_idx >= n:
        return True, None  # defensive: out-of-range → pass

    thresholds = _sanity_thresholds()
    low_price_threshold = thresholds["low_price_threshold"]
    K = thresholds["tail_discrepancy_k"]
    tail_min_bins = thresholds["tail_min_bins"]

    # Condition 2: edge bin must be sub-floor and quoted.
    px_edge = float(mkt_arr[edge_bin_idx])
    if not (0.0 < px_edge < low_price_threshold):
        return True, None  # well-priced or unquoted → pass

    # Condition 1: edge bin must be strictly on one side of the mode.
    mode_idx = int(np.argmax(p_cal_arr))
    if edge_bin_idx == mode_idx:
        return True, None  # edge IS the mode → pass

    if edge_bin_idx < mode_idx:
        side = "left"
        # Walk left from edge_bin_idx to 0, counting contiguous sub-floor-quoted bins.
        # Also walk right up to (but not including) mode_idx to complete the run.
        run_start = edge_bin_idx
        run_end = edge_bin_idx
        # Extend left
        while run_start > 0 and 0.0 < float(mkt_arr[run_start - 1]) < low_price_threshold:
            run_start -= 1
        # Extend right (up to mode-1)
        while run_end + 1 < mode_idx and 0.0 < float(mkt_arr[run_end + 1]) < low_price_threshold:
            run_end += 1
    else:
        side = "right"
        run_start = edge_bin_idx
        run_end = edge_bin_idx
        # Extend left (down to mode+1)
        while run_start - 1 > mode_idx and 0.0 < float(mkt_arr[run_start - 1]) < low_price_threshold:
            run_start -= 1
        # Extend right
        while run_end + 1 < n and 0.0 < float(mkt_arr[run_end + 1]) < low_price_threshold:
            run_end += 1

    run_length = run_end - run_start + 1

    # Condition 4: contiguous run must be >= tail_min_bins.
    if run_length < tail_min_bins:
        return True, None

    # Condition 3: per-bin ratio check.
    pc_edge = float(p_cal_arr[edge_bin_idx])
    ratio = pc_edge / px_edge
    if ratio < K:
        return True, None

    reason = (
        f"PROB_EDGE_BIN_TAIL_DISCREPANCY:{side}:idx={edge_bin_idx},"
        f"p_mkt={px_edge:.4f},p_cal={pc_edge:.4f},ratio={ratio:.2f},"
        f"run_length={run_length},mode_idx={mode_idx}"
    )
    return False, reason


# ---------------------------------------------------------------------------
# LIVE-PROB-P0 §B — Operator Binding Spec 2026-05-23
# ---------------------------------------------------------------------------

# Defaults for the new ``probability_edge_bin_sanity`` config block.
_DEFAULT_EDGE_BIN_MODE = "hard"
_DEFAULT_EDGE_BIN_LOW_PRICE_THRESHOLD = 0.05
_DEFAULT_EDGE_BIN_MIN_EDGE_GAP = 0.03
_DEFAULT_EDGE_BIN_ODDS_RATIO_THRESHOLD = 3.0
_DEFAULT_EDGE_BIN_MIN_MEMBER_SUPPORT = 0.05
_DEFAULT_EDGE_BIN_MIN_NEIGHBOR_SUPPORT = 0.05


def _edge_bin_sanity_thresholds() -> dict[str, Any]:
    """Read ``probability_edge_bin_sanity`` block from settings.json.

    Returns dict with all required threshold keys.  Absent block or absent key
    falls back to defaults — behavior is unchanged if block is missing.
    """
    block: dict[str, Any] = {}
    from src.config import settings

    try:
        raw = settings["probability_edge_bin_sanity"]
    except (KeyError, TypeError):
        raw = None
    if isinstance(raw, dict):
        block = raw
    return {
        "mode": str(block.get("mode", _DEFAULT_EDGE_BIN_MODE)),
        "low_price_threshold": float(
            block.get("low_price_threshold", _DEFAULT_EDGE_BIN_LOW_PRICE_THRESHOLD)
        ),
        "min_edge_gap": float(block.get("min_edge_gap", _DEFAULT_EDGE_BIN_MIN_EDGE_GAP)),
        "odds_ratio_threshold": float(
            block.get("odds_ratio_threshold", _DEFAULT_EDGE_BIN_ODDS_RATIO_THRESHOLD)
        ),
        "min_edge_bin_member_support": float(
            block.get("min_edge_bin_member_support", _DEFAULT_EDGE_BIN_MIN_MEMBER_SUPPORT)
        ),
        "min_neighbor_support": float(
            block.get("min_neighbor_support", _DEFAULT_EDGE_BIN_MIN_NEIGHBOR_SUPPORT)
        ),
    }


def probability_edge_bin_sanity(
    *,
    selected_bin_idx: int,
    bins: Sequence,
    p_raw: np.ndarray,
    p_cal: np.ndarray,
    p_market: np.ndarray | None,
    direction: str = "",
    metric: str = "",
    strategy_key: str = "",
    market_phase: str = "",
    config: dict | None = None,
) -> tuple[bool, str | None, dict]:
    """Gate 6 / LIVE-PROB-P0: per-edge-bin phantom predicate (operator binding spec §B).

    CRITICAL SAFETY OVERRIDE: if settled_member_support >= min_edge_bin_member_support,
    the gate passes unconditionally — even if ratio is high. This protects genuine
    BIMODAL edges where real ensemble members land in a low-priced secondary mode.
    A ratio-alone check would falsely block these under hard mode.

    settled_member_support = p_raw[selected_bin_idx]
      (fraction of MC-rounded members landing in the edge bin, pre-aggregated into p_raw)

    Reject ONLY when ALL of the following hold:
      1. 0 < p_market[edge] <= low_price_threshold (sub-floor quoted bin)
      2. p_cal[edge] - p_market[edge] >= min_edge_gap
      3. p_cal[edge] / max(p_market[edge], eps) >= odds_ratio_threshold (3.0)
      4. settled_member_support < min_edge_bin_member_support (0.05)
         [SAFETY: if support >= 0.05, return PASS immediately]
      5. edge bin sits in a contiguous sub-floor run >= tail_min_bins=2 on its side of mode

    Args:
        selected_bin_idx: index of the candidate edge bin (edge.support_index).
        bins: sequence of Bin-like objects (used for neighbor label only).
        p_raw: raw (MC-rounded) probability array; p_raw[i] = fraction of members in bin i.
        p_cal: calibrated probability array.
        p_market: per-bin market prices; None → always PASS.
        direction: "buy_yes" | "buy_no" | "" (for telemetry).
        metric: "high" | "low" | "" (for telemetry).
        strategy_key: opaque label (for telemetry).
        market_phase: opaque label (for telemetry).
        config: optional pre-loaded threshold dict (overrides settings.json; for testing).

    Returns:
        (True, None, telemetry_dict) — gate passes.
        (False, reason_code_str, telemetry_dict) — gate rejects; reason_code_str is one of:
          PROBABILITY_EDGE_BIN_UNSUPPORTED — no member support, tail position, ratio high
          PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT — low price + no support, market disagrees
          (shadow mode: reason_code_str = PROBABILITY_TAIL_SHAPE_ANOMALY_SHADOW)
          (hard mode: reason_code_str = PROBABILITY_TAIL_SHAPE_ANOMALY_HARD)
        telemetry_dict always has keys:
          edge_bin_idx, edge_bin_label, edge_bin_p_raw, edge_bin_p_cal, edge_bin_p_market,
          edge_bin_member_support, edge_bin_odds_ratio, near_tail_p_cal, near_tail_p_market,
          probability_sanity_mode, probability_sanity_reason
    """
    thresholds = config if config is not None else _edge_bin_sanity_thresholds()
    mode = thresholds["mode"]
    low_price_threshold = thresholds["low_price_threshold"]
    min_edge_gap = thresholds["min_edge_gap"]
    odds_ratio_threshold = thresholds["odds_ratio_threshold"]
    min_member_support = thresholds["min_edge_bin_member_support"]
    # tail_min_bins = 2: reuse same contiguity guard as legacy predicate
    tail_min_bins = 2

    p_cal_arr = np.asarray(p_cal, dtype=np.float64)
    p_raw_arr = np.asarray(p_raw, dtype=np.float64)
    n = len(p_cal_arr)

    # Build telemetry dict (always returned)
    bin_label = ""
    if 0 <= selected_bin_idx < len(bins):
        b = bins[selected_bin_idx]
        bin_label = getattr(b, "label", str(selected_bin_idx))

    px_edge = 0.0
    if p_market is not None and 0 <= selected_bin_idx < len(p_market):
        px_edge = float(np.asarray(p_market, dtype=np.float64)[selected_bin_idx])

    pc_edge = float(p_cal_arr[selected_bin_idx]) if 0 <= selected_bin_idx < n else 0.0
    pr_edge = float(p_raw_arr[selected_bin_idx]) if 0 <= selected_bin_idx < len(p_raw_arr) else 0.0
    eps = 1e-9
    odds_ratio = pc_edge / max(px_edge, eps)
    member_support = pr_edge  # p_raw[i] = fraction of MC-rounded members in bin i

    # Near-tail neighbor aggregation (for telemetry; immediate neighbors on tail side)
    mode_idx = int(np.argmax(p_cal_arr))
    near_tail_pcal = 0.0
    near_tail_pmkt = 0.0
    if p_market is not None:
        mkt_arr = np.asarray(p_market, dtype=np.float64)
        if selected_bin_idx < mode_idx and selected_bin_idx > 0:
            neighbor = selected_bin_idx - 1
            near_tail_pcal = float(p_cal_arr[neighbor])
            near_tail_pmkt = float(mkt_arr[neighbor])
        elif selected_bin_idx > mode_idx and selected_bin_idx < n - 1:
            neighbor = selected_bin_idx + 1
            near_tail_pcal = float(p_cal_arr[neighbor])
            near_tail_pmkt = float(mkt_arr[neighbor])

    telemetry: dict = {
        "edge_bin_idx": selected_bin_idx,
        "edge_bin_label": bin_label,
        "edge_bin_p_raw": pr_edge,
        "edge_bin_p_cal": pc_edge,
        "edge_bin_p_market": px_edge,
        "edge_bin_member_support": member_support,
        "edge_bin_odds_ratio": odds_ratio,
        "near_tail_p_cal": near_tail_pcal,
        "near_tail_p_market": near_tail_pmkt,
        "probability_sanity_mode": mode,
        "probability_sanity_reason": None,  # filled on rejection path
    }

    # --- Condition 1: edge bin must be quoted and sub-floor ---
    if p_market is None or selected_bin_idx < 0 or selected_bin_idx >= n:
        return True, None, telemetry  # no market data → pass

    mkt_arr = np.asarray(p_market, dtype=np.float64)
    if not (0.0 < px_edge <= low_price_threshold):
        return True, None, telemetry  # well-priced or unquoted → pass

    # --- Condition 4 (CRITICAL SAFETY): strong member support + market agreement → unconditional PASS ---
    # A genuine BIMODAL edge has BOTH real ensemble members AND a market that also
    # prices the secondary mode above the sub-floor threshold.
    # If p_market[edge] is ALSO sub-floor (< low_price_threshold), the market
    # disagrees with the member count — that is NOT a genuine bimodal edge.
    # Amsterdam case: p_raw[3]=0.220 (members exist) but p_mkt[3]=0.047 (sub-floor,
    # market skeptical) → NOT genuine bimodal → BIMODAL PROTECTION does NOT fire.
    if member_support >= min_member_support and px_edge >= low_price_threshold:
        return True, None, telemetry  # BIMODAL PROTECTION: members + market agree → pass

    # --- Condition 2: edge gap check ---
    edge_gap = pc_edge - px_edge
    if edge_gap < min_edge_gap:
        return True, None, telemetry  # gap too small → pass

    # --- Condition 3: per-bin odds-ratio check ---
    if odds_ratio < odds_ratio_threshold:
        return True, None, telemetry  # ratio below threshold → pass

    # --- Condition 5: contiguous sub-floor run >= tail_min_bins on tail side of mode ---
    if selected_bin_idx == mode_idx:
        return True, None, telemetry  # edge IS the mode → pass

    sub_floor_mask = (mkt_arr > 0.0) & (mkt_arr <= low_price_threshold)

    if selected_bin_idx < mode_idx:
        side = "left"
        run_start = selected_bin_idx
        run_end = selected_bin_idx
        while run_start > 0 and sub_floor_mask[run_start - 1]:
            run_start -= 1
        while run_end + 1 < mode_idx and sub_floor_mask[run_end + 1]:
            run_end += 1
    else:
        side = "right"
        run_start = selected_bin_idx
        run_end = selected_bin_idx
        while run_start - 1 > mode_idx and sub_floor_mask[run_start - 1]:
            run_start -= 1
        while run_end + 1 < n and sub_floor_mask[run_end + 1]:
            run_end += 1

    run_length = run_end - run_start + 1
    if run_length < tail_min_bins:
        return True, None, telemetry  # isolated bin → pass

    # --- All conditions met: phantom detected ---
    detail = (
        f"{side}:idx={selected_bin_idx},"
        f"p_raw={pr_edge:.4f},p_mkt={px_edge:.4f},p_cal={pc_edge:.4f},"
        f"ratio={odds_ratio:.2f},support={member_support:.4f},"
        f"run_length={run_length},mode_idx={mode_idx}"
    )

    if mode == "hard":
        reason_code = "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD"
    else:
        reason_code = "PROBABILITY_TAIL_SHAPE_ANOMALY_SHADOW"

    # Enrich with specific code based on support/price pattern
    if member_support < min_member_support and 0.0 < px_edge <= low_price_threshold:
        # Low price + no member support = strongest phantom signal
        reason_code = "PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT"
    elif member_support < min_member_support:
        reason_code = "PROBABILITY_EDGE_BIN_UNSUPPORTED"

    full_reason = f"{reason_code}:{detail}"
    telemetry["probability_sanity_reason"] = full_reason
    return False, full_reason, telemetry
