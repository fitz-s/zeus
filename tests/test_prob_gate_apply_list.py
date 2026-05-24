# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: LIVE-PROB-P0 Gate 6 apply-list enforcement (post-merge correctness gap #1)
# Purpose: RED→GREEN antibody tests for apply_to_strategies + apply_to_metrics enforcement.
#   RED on origin/main: gate hard-rejects LOW metric and unlisted strategies (unvalidated).
#   GREEN on branch: gate downgrades to SHADOW for unvalidated (metric, strategy) pairs.
"""Apply-list enforcement tests for probability_edge_bin_sanity (LIVE-PROB-P0 Gate 6).

Covers:
  (a) HIGH + opening_hunt (Amsterdam) → HARD reject (apply-list satisfied, unchanged behavior)
  (b) LOW + opening_hunt_low phantom → SHADOW only (LOW not in apply_to_metrics)
  (c) HIGH + unlisted strategy + phantom fixture → SHADOW only (strategy not in apply_to_strategies)
  (d) Both apply_to lists empty (back-compat) → mode from thresholds["mode"] (hard = hard reject)

RED proof: on origin/main, cases (b) and (c) return ok=False with mode=hard — hard blocking
  unvalidated combinations. GREEN after patch: mode is "shadow", function still returns ok=False
  (the "reject" signal) but the evaluator caller sees mode="shadow" and does NOT set
  should_trade=False.

Note: probability_edge_bin_sanity returns (ok=False, reason, telemetry) even in shadow mode —
  the shadow/hard distinction is in telemetry["probability_sanity_mode"].  The evaluator
  only hard-blocks when _eb_gate_mode == "hard".  These tests verify the mode field.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.signal.probability_sanity import probability_edge_bin_sanity


def _make_bins(n: int) -> list:
    from src.types.market import Bin
    return [Bin(low=float(i), high=float(i), unit="C", label=f"bin_{i}") for i in range(n)]


# Amsterdam fixture (canonical; from probability_trace_fact probtrace:3d2f2373-8c8)
_AMS_P_RAW = np.array([0.0, 0.0, 0.0123, 0.2203, 0.5358, 0.2094, 0.02, 0.0022, 0.0, 0.0, 0.0])
_AMS_P_CAL = np.array([0.008, 0.008, 0.0098, 0.1856, 0.5656, 0.1754, 0.0158, 0.008, 0.008, 0.008, 0.008])
_AMS_P_MKT = np.array([0.002, 0.0034, 0.0066, 0.0465, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

# Config with apply-lists (mirrors production config/settings.json apply_to_metrics/apply_to_strategies)
_CONFIG_WITH_APPLY_LISTS = {
    "mode": "hard",
    "low_price_threshold": 0.05,
    "min_edge_gap": 0.03,
    "odds_ratio_threshold": 3.0,
    "min_edge_bin_member_support": 0.05,
    "min_neighbor_support": 0.05,
    "apply_to_strategies": [
        "opening_hunt",
        "opening_hunt_low",
        "update_reaction",
        "update_reaction_low",
        "settlement_capture",
        "imminent_open_capture",
    ],
    "apply_to_metrics": ["high"],
}

# Config WITHOUT apply-lists (back-compat: empty lists mean "apply to all")
_CONFIG_NO_APPLY_LISTS = {
    "mode": "hard",
    "low_price_threshold": 0.05,
    "min_edge_gap": 0.03,
    "odds_ratio_threshold": 3.0,
    "min_edge_bin_member_support": 0.05,
    "min_neighbor_support": 0.05,
    # apply_to_strategies and apply_to_metrics intentionally absent
}

# Phantom fixture for LOW metric: left-tail phantom, 5 bins, mode at idx 3.
# p_raw=0 in left tail bins, p_cal high, p_mkt sub-floor, run_length=2.
_LOW_P_RAW = np.array([0.0, 0.0, 0.05, 0.70, 0.25])
_LOW_P_CAL = np.array([0.15, 0.14, 0.05, 0.50, 0.16])
_LOW_P_MKT = np.array([0.018, 0.025, 0.05, 0.55, 0.30])  # bins 0,1 sub-floor
# edge_bin_idx=0: p_raw=0, p_cal=0.15, p_mkt=0.018, ratio=8.3, gap=0.132, run_length>=2 → would reject


def test_high_opening_hunt_amsterdam_still_hard_rejected():
    """HIGH + opening_hunt (Amsterdam) → HARD reject even with apply-lists.

    Both conditions met: metric="high" IN apply_to_metrics, strategy_key="opening_hunt" IN
    apply_to_strategies. Mode stays "hard". Rejection must be HARD, not shadow.

    RED on origin/main if apply-list logic is added wrong and breaks Amsterdam.
    GREEN: ok=False AND telemetry["probability_sanity_mode"] == "hard".
    """
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=_AMS_P_MKT,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG_WITH_APPLY_LISTS,
    )
    assert ok is False, (
        f"Amsterdam (HIGH+opening_hunt) must be HARD rejected. got ok=True, reason={reason!r}"
    )
    assert telemetry["probability_sanity_mode"] == "hard", (
        f"Mode must be 'hard' for HIGH+opening_hunt; got {telemetry['probability_sanity_mode']!r}"
    )
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in (reason or ""), (
        f"Reason must indicate hard rejection: {reason!r}"
    )


def test_low_metric_phantom_shadow_only():
    """LOW metric phantom → SHADOW (log-only, NOT hard block).

    LOW is not in apply_to_metrics=["high"]. Even though the phantom is real,
    mode is downgraded to shadow. The function signals rejection (ok=False) but
    telemetry["probability_sanity_mode"] == "shadow" so the evaluator does NOT
    set should_trade=False.

    RED on origin/main: mode="hard" (unvalidated LOW hard-blocked).
    GREEN on branch: mode="shadow".
    """
    bins = _make_bins(len(_LOW_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=0,
        bins=bins,
        p_raw=_LOW_P_RAW,
        p_cal=_LOW_P_CAL,
        p_market=_LOW_P_MKT,
        direction="buy_yes",
        metric="low",  # NOT in apply_to_metrics
        strategy_key="opening_hunt",
        config=_CONFIG_WITH_APPLY_LISTS,
    )
    # Gate still fires the phantom detection (ok=False) but mode must be shadow
    # (evaluator only hard-blocks when mode=="hard")
    assert ok is False, (
        f"LOW metric phantom is still a phantom (ok should be False). got ok=True"
    )
    assert telemetry["probability_sanity_mode"] == "shadow", (
        f"LOW metric must run in SHADOW mode (unvalidated). "
        f"got mode={telemetry['probability_sanity_mode']!r}. "
        f"RED: origin/main runs all non-day0 edges as hard regardless of metric."
    )
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_SHADOW" in (reason or ""), (
        f"Shadow reason code expected; got {reason!r}"
    )


def test_unlisted_strategy_phantom_shadow_only():
    """Strategy not in apply_to_strategies → SHADOW.

    "opening_inertia" is a real strategy but NOT in apply_to_strategies.
    Same phantom fixture as Amsterdam but metric=high, strategy=opening_inertia.
    Mode must be shadow regardless of metric.

    RED on origin/main: mode="hard" (strategy enforcement absent).
    GREEN on branch: mode="shadow".
    """
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=_AMS_P_MKT,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_inertia",  # NOT in apply_to_strategies
        config=_CONFIG_WITH_APPLY_LISTS,
    )
    assert ok is False, (
        f"Phantom with unlisted strategy is still a phantom (ok should be False). got ok=True"
    )
    assert telemetry["probability_sanity_mode"] == "shadow", (
        f"Unlisted strategy must run in SHADOW mode. "
        f"got mode={telemetry['probability_sanity_mode']!r}. "
        f"RED: origin/main runs all non-day0 as hard regardless of strategy_key."
    )


def test_no_apply_lists_back_compat_hard_rejects():
    """Empty apply-lists → back-compat: mode from thresholds["mode"] (hard=hard reject).

    When apply_to_strategies and apply_to_metrics are absent from config,
    the gate applies to ALL strategy+metric combinations — same as pre-apply-list behavior.

    Verifies no regression for configs without apply-list keys.
    """
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=_AMS_P_MKT,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG_NO_APPLY_LISTS,  # no apply-lists → no filtering
    )
    assert ok is False, (
        f"Without apply-lists, Amsterdam must still be HARD rejected. got ok=True"
    )
    assert telemetry["probability_sanity_mode"] == "hard", (
        f"Without apply-lists, mode stays 'hard' from thresholds. "
        f"got {telemetry['probability_sanity_mode']!r}"
    )


def test_opening_hunt_low_high_metric_hard_rejects():
    """opening_hunt_low + HIGH metric → HARD reject (both in apply-lists).

    opening_hunt_low IS in apply_to_strategies; high IS in apply_to_metrics.
    Must not be silently shadowed.
    """
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=_AMS_P_MKT,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt_low",  # IN apply_to_strategies
        config=_CONFIG_WITH_APPLY_LISTS,
    )
    assert ok is False, f"opening_hunt_low+HIGH phantom must be HARD rejected. reason={reason!r}"
    assert telemetry["probability_sanity_mode"] == "hard", (
        f"Mode must be 'hard' for listed strategy + listed metric. "
        f"got {telemetry['probability_sanity_mode']!r}"
    )


def test_opening_hunt_low_low_metric_shadow_only():
    """opening_hunt_low + LOW metric → SHADOW.

    Strategy IS in apply_to_strategies but metric (low) is NOT in apply_to_metrics.
    AND-logic: BOTH must match for hard. LOW → shadow.
    """
    bins = _make_bins(len(_LOW_P_RAW))
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=0,
        bins=bins,
        p_raw=_LOW_P_RAW,
        p_cal=_LOW_P_CAL,
        p_market=_LOW_P_MKT,
        direction="buy_yes",
        metric="low",
        strategy_key="opening_hunt_low",  # IN apply_to_strategies but metric=low NOT in apply_to_metrics
        config=_CONFIG_WITH_APPLY_LISTS,
    )
    assert ok is False, f"Phantom with LOW metric still fires (ok should be False). got ok=True"
    assert telemetry["probability_sanity_mode"] == "shadow", (
        f"opening_hunt_low + LOW metric → SHADOW (metric not validated). "
        f"got mode={telemetry['probability_sanity_mode']!r}"
    )
