# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: LIVE-PROB-P0 Gate 6 apply-metrics enforcement (post-merge correctness gap #1)
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: RED→GREEN antibody tests for apply_to_metrics enforcement (metric-only scope).
#   apply_to_strategies is advisory metadata, NOT a hard filter.
#   RED on origin/main: gate hard-rejects LOW metric (unvalidated; no apply-metrics check).
#   GREEN on branch: LOW → SHADOW; all HIGH strategies (incl. opening_inertia) → HARD.
# Reuse: Run when changing probability_edge_bin_sanity apply_to_metrics enforcement logic,
#   or when modifying apply_to_strategies advisory semantics. Verify RED/GREEN baseline
#   before trusting results (see module docstring for RED proof on origin/main).
"""Apply-metrics enforcement tests for probability_edge_bin_sanity (LIVE-PROB-P0 Gate 6).

Covers:
  (a) HIGH + opening_hunt Amsterdam → HARD reject (apply-list satisfied, unchanged)
  (b) HIGH + opening_inertia Amsterdam phantom → HARD reject (CRITICAL: must not be shadowed)
  (c) HIGH + imminent_open_capture phantom → HARD reject (any strategy, HIGH = hard)
  (d) HIGH + center_buy phantom → HARD (not in advisory list, still hard)
  (e) LOW + opening_hunt phantom → SHADOW (LOW unvalidated per critic M1)
  (f) LOW + opening_hunt_low phantom → SHADOW (strategy in advisory list; metric=LOW = shadow)
  (g) empty/absent apply_to_metrics → back-compat: hard for all metrics

RED proof: origin/main has zero apply_to_metrics enforcement. Tests (e) and (f) fail on
  origin/main (mode="hard" for LOW). The prior branch's AND-filter would have failed test
  (b) (opening_inertia not in apply_to_strategies → shadow = phantom hole on dominant strategy).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

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

# Config with apply_to_metrics only (strategy list is advisory — not used as gate filter)
_CONFIG_WITH_METRICS = {
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
        # opening_inertia intentionally absent to prove strategy list is NOT a gate filter
    ],
    "apply_to_metrics": ["high"],
}

# Config WITHOUT apply-lists (back-compat)
_CONFIG_NO_APPLY = {
    "mode": "hard",
    "low_price_threshold": 0.05,
    "min_edge_gap": 0.03,
    "odds_ratio_threshold": 3.0,
    "min_edge_bin_member_support": 0.05,
    "min_neighbor_support": 0.05,
}

# LOW phantom fixture: left-tail, mode at idx 3, run_length>=2, ratio>>3
_LOW_P_RAW = np.array([0.0, 0.0, 0.05, 0.70, 0.25])
_LOW_P_CAL = np.array([0.15, 0.14, 0.05, 0.50, 0.16])
_LOW_P_MKT = np.array([0.018, 0.025, 0.05, 0.55, 0.30])


def test_high_opening_hunt_amsterdam_hard_rejected():
    """HIGH + opening_hunt → HARD (baseline, unchanged)."""
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=3, bins=bins, p_raw=_AMS_P_RAW, p_cal=_AMS_P_CAL, p_market=_AMS_P_MKT,
        metric="high", strategy_key="opening_hunt", config=_CONFIG_WITH_METRICS,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "hard", f"Expected hard, got {t['probability_sanity_mode']!r}"
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in (reason or ""), reason


def test_high_opening_inertia_amsterdam_hard_rejected():
    """CRITICAL: HIGH + opening_inertia → HARD reject.

    opening_inertia is absent from apply_to_strategies (advisory list). With the old
    AND-filter, this would have been shadowed — a phantom hole on the dominant live
    strategy (~547 candidates in 2 days). Metrics-only enforcement means strategy_key
    is irrelevant: any HIGH phantom is hard-rejected regardless of strategy.

    RED on prior branch (AND-filter): mode="shadow".
    RED on origin/main: no enforcement at all, but LOW tests fail.
    GREEN on this branch: mode="hard".
    """
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=3, bins=bins, p_raw=_AMS_P_RAW, p_cal=_AMS_P_CAL, p_market=_AMS_P_MKT,
        metric="high",
        strategy_key="opening_inertia",  # NOT in apply_to_strategies advisory list
        config=_CONFIG_WITH_METRICS,
    )
    assert ok is False, f"opening_inertia+HIGH Amsterdam must be HARD rejected; got ok=True"
    assert t["probability_sanity_mode"] == "hard", (
        f"opening_inertia+HIGH must be HARD (strategy list is advisory only). "
        f"got mode={t['probability_sanity_mode']!r}. "
        f"Prior AND-filter silently created a phantom hole on the dominant live strategy."
    )
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in (reason or ""), reason


def test_high_imminent_open_capture_hard_rejected():
    """HIGH + imminent_open_capture → HARD."""
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=3, bins=bins, p_raw=_AMS_P_RAW, p_cal=_AMS_P_CAL, p_market=_AMS_P_MKT,
        metric="high", strategy_key="imminent_open_capture", config=_CONFIG_WITH_METRICS,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "hard", f"got {t['probability_sanity_mode']!r}"


def test_high_center_buy_hard_rejected():
    """HIGH + center_buy (not in advisory list) → HARD."""
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=3, bins=bins, p_raw=_AMS_P_RAW, p_cal=_AMS_P_CAL, p_market=_AMS_P_MKT,
        metric="high", strategy_key="center_buy", config=_CONFIG_WITH_METRICS,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "hard", f"got {t['probability_sanity_mode']!r}"


def test_low_metric_phantom_shadow_only():
    """LOW metric phantom → SHADOW (log-only, NOT hard block).

    RED on origin/main: mode="hard" (no apply_to_metrics check).
    GREEN on branch: mode="shadow".
    """
    bins = _make_bins(len(_LOW_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=0, bins=bins, p_raw=_LOW_P_RAW, p_cal=_LOW_P_CAL, p_market=_LOW_P_MKT,
        metric="low", strategy_key="opening_hunt", config=_CONFIG_WITH_METRICS,
    )
    assert ok is False, f"LOW phantom still fires (ok=False); got ok=True"
    assert t["probability_sanity_mode"] == "shadow", (
        f"LOW metric must be SHADOW (unvalidated). got {t['probability_sanity_mode']!r}. "
        f"RED: origin/main has no apply_to_metrics enforcement."
    )
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_SHADOW" in (reason or ""), reason


def test_low_metric_opening_hunt_low_shadow_only():
    """LOW + opening_hunt_low → SHADOW (strategy in advisory list; metric=LOW overrides to shadow)."""
    bins = _make_bins(len(_LOW_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=0, bins=bins, p_raw=_LOW_P_RAW, p_cal=_LOW_P_CAL, p_market=_LOW_P_MKT,
        metric="low", strategy_key="opening_hunt_low", config=_CONFIG_WITH_METRICS,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "shadow", f"got {t['probability_sanity_mode']!r}"


def test_no_apply_metrics_back_compat_hard_high():
    """Absent apply_to_metrics → back-compat: mode from thresholds (hard for HIGH)."""
    bins = _make_bins(len(_AMS_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=3, bins=bins, p_raw=_AMS_P_RAW, p_cal=_AMS_P_CAL, p_market=_AMS_P_MKT,
        metric="high", strategy_key="opening_hunt", config=_CONFIG_NO_APPLY,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "hard", f"got {t['probability_sanity_mode']!r}"


def test_no_apply_metrics_back_compat_hard_low():
    """Absent apply_to_metrics → back-compat: mode from thresholds (hard for LOW too)."""
    bins = _make_bins(len(_LOW_P_RAW))
    ok, reason, t = probability_edge_bin_sanity(
        selected_bin_idx=0, bins=bins, p_raw=_LOW_P_RAW, p_cal=_LOW_P_CAL, p_market=_LOW_P_MKT,
        metric="low", strategy_key="opening_hunt", config=_CONFIG_NO_APPLY,
    )
    assert ok is False
    assert t["probability_sanity_mode"] == "hard", f"got {t['probability_sanity_mode']!r}"
