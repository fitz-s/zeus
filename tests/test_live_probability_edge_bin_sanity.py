# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: /Users/leofitz/.claude/jobs/866db2ea/IMPL_SPEC_operator.md §B §D §D3 §D4
"""Tests for probability_edge_bin_sanity (LIVE-PROB-P0 §B operator binding spec).

Coverage:
  (a) Amsterdam RED — predicate rejects Amsterdam fixture (p_raw[edge]>=0.05 but
      p_mkt[edge] also sub-floor → BIMODAL PROTECTION does NOT fire).
  (b) LEGIT BIMODAL PASS — genuine bimodal: p_raw[edge]>=0.05 AND p_mkt[edge]>=0.05
      (market also prices the secondary mode) → BIMODAL PROTECTION fires → PASS.
  (c) §D3 LOW symmetry — right-tail phantom also rejected; test structure
      FAILS if only left-tail mask is checked.
  (d) p_raw=0, sub-floor, ratio high → REJECTED.
  (e) Well-priced edge (p_mkt >= 0.05) → PASS regardless of ratio.
  (f) Telemetry keys all present on both pass and reject paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.signal.probability_sanity import probability_edge_bin_sanity
from src.types.market import Bin


def _make_bins(n: int) -> list:
    return [Bin(low=float(i), high=float(i), unit="C", label=f"bin_{i}") for i in range(n)]


# Amsterdam fixture (from probability_trace_fact probtrace:3d2f2373-8c8)
_AMS_P_RAW = np.array([0.0, 0.0, 0.0123, 0.2203, 0.5358, 0.2094, 0.02, 0.0022, 0.0, 0.0, 0.0])
_AMS_P_CAL = np.array([0.008, 0.008, 0.0098, 0.1856, 0.5656, 0.1754, 0.0158, 0.008, 0.008, 0.008, 0.008])
_AMS_P_MKT = np.array([0.002, 0.0034, 0.0066, 0.0465, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

_CONFIG = {
    "mode": "hard",
    "low_price_threshold": 0.05,
    "min_edge_gap": 0.03,
    "odds_ratio_threshold": 3.0,
    "min_edge_bin_member_support": 0.05,
    "min_neighbor_support": 0.05,
}


def test_amsterdam_rejected():
    """Amsterdam fixture MUST be rejected.

    edge_bin_idx=3: p_raw=0.2203 (members exist) but p_mkt=0.0465 (sub-floor).
    BIMODAL PROTECTION requires BOTH p_raw >= 0.05 AND p_mkt >= 0.05.
    Since p_mkt=0.0465 < 0.05, BIMODAL PROTECTION does NOT fire.
    ratio=0.1856/0.0465=3.99 >= 3.0, run_length=4 >= 2 → REJECT.
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
        config=_CONFIG,
    )
    assert ok is False, f"Amsterdam must be REJECTED; got ok=True, reason={reason!r}"
    assert reason is not None
    # reason code should be PROBABILITY_TAIL_SHAPE_ANOMALY_HARD (hard mode, sub-floor+members)
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in reason or "PROBABILITY_LOW_PRICE" in reason, (
        f"Unexpected reason code: {reason!r}"
    )
    assert telemetry["edge_bin_idx"] == 3
    assert telemetry["edge_bin_member_support"] == pytest.approx(0.2203, abs=1e-3)


def test_bimodal_protection_fires_when_market_agrees():
    """Genuine bimodal edge: p_raw[edge]>=0.05 AND p_mkt[edge]>=0.05 → PASS unconditionally.

    This case: market prices the secondary mode above threshold (p_mkt=0.08 > 0.05).
    Even though ratio=0.22/0.08=2.75 is below threshold, the BIMODAL PROTECTION
    fires because BOTH member support AND market price are above threshold.
    """
    # 5-bin setup: mode at idx 3, secondary mode at idx 0 (bimodal)
    p_raw = np.array([0.12, 0.02, 0.01, 0.70, 0.15])
    p_cal = np.array([0.22, 0.02, 0.01, 0.60, 0.15])
    p_mkt = np.array([0.08, 0.01, 0.01, 0.55, 0.15])  # p_mkt[0]=0.08 >= 0.05
    bins = _make_bins(5)
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=0,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    assert ok is True, (
        f"Genuine bimodal (market also prices it) must PASS. got ok=False, reason={reason!r}"
    )
    assert reason is None


def test_bimodal_protection_does_not_fire_when_market_sub_floor():
    """BIMODAL PROTECTION requires BOTH p_raw >= 0.05 AND p_mkt >= 0.05.

    If p_mkt[edge] is sub-floor, the market disagrees — not a genuine bimodal edge.
    """
    p_raw = np.array([0.20, 0.02, 0.01, 0.62, 0.15])
    p_cal = np.array([0.20, 0.02, 0.01, 0.62, 0.15])
    p_mkt = np.array([0.03, 0.01, 0.01, 0.60, 0.15])  # p_mkt[0]=0.03 < 0.05 (sub-floor)
    bins = _make_bins(5)
    # ratio=0.20/0.03=6.67, run_length=1 (only idx=0 is sub-floor left of mode=3)
    # run_length=1 < tail_min_bins=2 → pass on condition 5 alone
    # But test the bimodal protection branch: with p_mkt sub-floor, protection doesn't fire
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=0,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    # run_length=1 → condition 5 prevents rejection; but BIMODAL PROTECTION is NOT the reason
    # The important assertion: if we set run_length>=2, it should reject
    # (bimodal protection does not save it when p_mkt is sub-floor)
    # Use a layout with run_length >= 2:
    p_mkt2 = np.array([0.03, 0.04, 0.01, 0.60, 0.15])  # bins 0,1 both sub-floor
    ok2, reason2, _ = probability_edge_bin_sanity(
        selected_bin_idx=0,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt2,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    # p_raw[0]=0.20>=0.05 but p_mkt[0]=0.03<0.05 → BIMODAL PROTECTION does NOT fire → reject
    assert ok2 is False, (
        f"With p_mkt sub-floor, BIMODAL PROTECTION must NOT fire; got ok=True"
    )


def test_right_tail_phantom_rejected():
    """§D3 LOW symmetry: right-tail phantom also rejected.

    Layout: mode at idx 1 (lowest HIGH bin), phantom bins 2-4 on RIGHT side.
    If the implementation only checks the LEFT tail mask, this test will FAIL —
    the right-tail rejection won't fire.
    """
    # 5-bin HIGH setup; mode at idx 1; right-tail phantom at bins 2,3,4
    p_raw = np.array([0.15, 0.70, 0.0, 0.0, 0.0])      # no members in right tail
    p_cal = np.array([0.05, 0.55, 0.15, 0.14, 0.11])    # right tail has ~40% p_cal
    p_mkt = np.array([0.10, 0.55, 0.016, 0.014, 0.010]) # right bins 2-4 all sub-floor
    bins = _make_bins(5)
    # edge_bin_idx=4 (rightmost): p_mkt=0.010 sub-floor, p_raw=0, ratio=0.11/0.010=11.0
    # run: bins 2,3,4 all sub-floor → run_length=3 >= 2 → REJECT
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=4,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    assert ok is False, (
        f"Right-tail phantom must be REJECTED (§D3 symmetry). "
        f"If ok=True, the implementation only checks left tail. reason={reason!r}"
    )
    assert "right" in (reason or "").lower() or "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in (reason or ""), (
        f"Reason should mention right-side rejection: {reason!r}"
    )


def test_no_member_support_rejected():
    """p_raw[edge]=0: no members at all in edge bin → REJECT."""
    p_raw = np.array([0.0, 0.0, 0.60, 0.40, 0.0])
    p_cal = np.array([0.008, 0.008, 0.55, 0.40, 0.008])
    p_mkt = np.array([0.010, 0.020, 0.55, 0.40, 0.0])  # bins 0,1 sub-floor; edge=1
    bins = _make_bins(5)
    ok, reason, _ = probability_edge_bin_sanity(
        selected_bin_idx=1,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    # ratio=0.008/0.020=0.4 < 3.0 → passes condition 3 → overall PASS
    # This case: ratio too low. Use a higher ratio:
    p_cal2 = np.array([0.008, 0.12, 0.55, 0.30, 0.008])
    ok2, reason2, _ = probability_edge_bin_sanity(
        selected_bin_idx=1,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal2,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    # p_raw[1]=0, p_mkt[1]=0.02, p_cal[1]=0.12; ratio=6.0>=3; gap=0.10>=0.03;
    # run_length=2 (bins 0,1 sub-floor) >= 2 → REJECT
    assert ok2 is False, f"Zero member support must be REJECTED. reason={reason2!r}"


def test_well_priced_edge_passes():
    """p_mkt[edge] >= 0.05 → PASS (condition 1 fails → pass)."""
    p_raw = np.array([0.0, 0.10, 0.60, 0.30, 0.0])
    p_cal = np.array([0.008, 0.20, 0.55, 0.20, 0.008])
    p_mkt = np.array([0.0, 0.08, 0.55, 0.35, 0.0])  # p_mkt[1]=0.08 >= 0.05
    bins = _make_bins(5)
    ok, reason, telemetry = probability_edge_bin_sanity(
        selected_bin_idx=1,
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_mkt,
        direction="buy_yes",
        metric="high",
        strategy_key="opening_hunt",
        config=_CONFIG,
    )
    assert ok is True, f"Well-priced edge must PASS. reason={reason!r}"
    assert reason is None


def test_telemetry_keys_always_present():
    """Telemetry dict always has all 11 required keys — both on pass and reject paths."""
    required_keys = {
        "edge_bin_idx",
        "edge_bin_label",
        "edge_bin_p_raw",
        "edge_bin_p_cal",
        "edge_bin_p_market",
        "edge_bin_member_support",
        "edge_bin_odds_ratio",
        "near_tail_p_cal",
        "near_tail_p_market",
        "probability_sanity_mode",
        "probability_sanity_reason",
    }
    bins = _make_bins(len(_AMS_P_RAW))

    # Reject path (Amsterdam)
    _, _, t_reject = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=_AMS_P_MKT,
        config=_CONFIG,
    )
    missing = required_keys - set(t_reject.keys())
    assert not missing, f"Telemetry missing keys on reject path: {missing}"

    # Pass path (well-priced)
    p_mkt_pass = np.zeros_like(_AMS_P_MKT)
    p_mkt_pass[3] = 0.10  # well-priced
    p_mkt_pass[4] = 0.50
    _, _, t_pass = probability_edge_bin_sanity(
        selected_bin_idx=3,
        bins=bins,
        p_raw=_AMS_P_RAW,
        p_cal=_AMS_P_CAL,
        p_market=p_mkt_pass,
        config=_CONFIG,
    )
    missing_pass = required_keys - set(t_pass.keys())
    assert not missing_pass, f"Telemetry missing keys on pass path: {missing_pass}"
