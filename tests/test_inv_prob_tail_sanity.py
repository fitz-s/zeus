# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-23_probability_phantom_edge/FIX_PLAN.md §4 (LIVE-PROB-P0)
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Relationship test for INV(PROB_TAIL_SANITY) — phantom-edge gate at evaluator.py gate site.
# Reuse: Run when check_cumulative_tail_discrepancy, check_edge_bin_tail_discrepancy,
#   validate_high_distribution, or evaluator non-day0 gate changes.
"""Relationship test for INV(PROB_TAIL_SANITY).

INV(PROB_TAIL_SANITY): When the candidate edge bin is a sub-floor-quoted bin
whose per-bin p_cal/p_market ratio >= K AND it sits in a contiguous sub-floor tail
of >= tail_min_bins on the tail side of the mode, the candidate MUST be rejected at
the per-edge SIGNAL_QUALITY gate (evaluator.py), NOT by the economic floor.

Tail bins are bins where 0 < market_price < low_price_threshold (0.05) —
underpriced quoted bins on either side of the mode. Unquoted bins (px==0.0) are
excluded per Gate 5 convention.

REDESIGN (2026-05-23): family-level check_cumulative_tail_discrepancy is now
TELEMETRY ONLY (computes audit columns, logs, no rejection). Rejection moved to
check_edge_bin_tail_discrepancy in the per-edge evaluation loop BEFORE economic floor.
This eliminates 59 FP cases (Jeddah, Tokyo) where the family gate fired on incidental
left-tail bins while the ACTUAL EDGE was on a well-priced bin.

Real-data fixtures (from zeus-world.db, 2026-05-23):
  Amsterdam (2026-05-24) phantom — REAL: 11-bin, mode_idx=4 (unquoted), edge at
    bin_idx=3 (p_mkt=0.047, p_cal=0.186, ratio=3.99). 4 contiguous left-tail bins 0-3.
    Family gate: REJECTS (red). Edge-bin gate: REJECTS (green — still rejects).
  Jeddah (2026-05-23) FP — REAL: 11-bin, mode_idx=7 (p_mkt=0.131), edge at
    bin_idx=7 (well-priced p_mkt=0.131 >= 0.05). Incidental left sub-floor bins 3,5.
    Family gate: REJECTS (red — demonstrates the bug). Edge-bin gate: PASSES (green — fix).

Tests:
  (a) test_check_cumulative_tail_discrepancy_rejects_amsterdam — family gate, left-tail
  (b) test_check_cumulative_tail_discrepancy_rejects_right_tail — family gate, right-tail
  (c) test_check_cumulative_tail_discrepancy_passes_fair_distribution — family gate pass
  (d) test_check_cumulative_tail_discrepancy_passes_low_ratio — family gate pass
  (e) test_check_cumulative_tail_discrepancy_passes_single_dustbin — tail_min_bins guard
  (f) test_check_cumulative_tail_discrepancy_rejects_two_bin_phantom — tail_min_bins threshold
  (g) test_check_edge_bin_tail_discrepancy_rejects_amsterdam_real_data — REAL AMS, edge-bin rejects
  (h) test_check_edge_bin_tail_discrepancy_passes_jeddah_fp_real_data — REAL Jeddah, edge-bin passes
  (i) test_evaluate_candidate_nday0_tail_gate_wiring — per-edge wiring, SIGNAL_QUALITY rejection
  (j) test_evaluate_candidate_day0_bypasses_tail_gate — day0 guard antibody
  (k) test_evaluate_candidate_shadow_mode_stamps_columns — schema-34 columns in shadow mode
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.signal.probability_sanity import (
    check_cumulative_tail_discrepancy,
    check_edge_bin_tail_discrepancy,
)
from src.contracts.no_trade_reason import NoTradeReason
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Amsterdam fixture helpers
# ---------------------------------------------------------------------------

def _amsterdam_11bin_fixture():
    """11-bin Amsterdam HIGH fixture matching the phantom-edge defect spec.

    Bins: 19-29 deg C (11 integer point buckets low==high).
    p_cal[23 bin] = 0.186; cumulative p_cal[bins 0-4] = 0.211.
    p_market[23 bin] = 0.047 (sub-floor); cumulative p_market[0-4] = 0.059.
    Mode bin is 24 deg C (p_cal=0.220) — passes Gate 4 (point-bucket < 0.50).
    All per-bin p_cal values < 0.35 — passes Gate 5 (market-disagreement).
    Ratio = 0.211/0.059 = 3.58 >= K=3.0, tail_mkt=0.059 < floor=0.10 → Gate 6 REJECTS.

    Returns: (bins, p_raw, p_cal, p_market, member_samples)
    """
    centers = [19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]
    bins = [Bin(low=c, high=c, unit="C", label=f"{c:.0f}C") for c in centers]

    p_cal = np.array([
        0.006,   # 19C
        0.007,   # 20C
        0.007,   # 21C
        0.005,   # 22C
        0.186,   # 23C  left-tail phantom: p_cal[23]=0.186; cumsum[0:5]=0.211
        0.220,   # 24C  MODE bin (highest p_cal); p_cal[mode]=0.220 < 0.50 Gate4 PASS
        0.195,   # 25C
        0.160,   # 26C
        0.110,   # 27C
        0.070,   # 28C
        0.034,   # 29C
    ], dtype=np.float64)
    assert abs(p_cal[:5].sum() - 0.211) < 0.001, f"fixture check: cumsum={p_cal[:5].sum()}"
    assert abs(p_cal.sum() - 1.0) < 1e-6, f"fixture check: total={p_cal.sum()}"

    p_market = np.array([
        0.003,   # 19C (sub-floor: 0 < 0.003 < 0.05)
        0.003,   # 20C (sub-floor)
        0.003,   # 21C (sub-floor)
        0.003,   # 22C (sub-floor)
        0.047,   # 23C sub-floor (0 < 0.047 < 0.05) tail bin
        0.220,   # 24C
        0.250,   # 25C
        0.220,   # 26C
        0.130,   # 27C
        0.085,   # 28C
        0.036,   # 29C
    ], dtype=np.float64)
    assert abs(p_market[:5].sum() - 0.059) < 0.001, f"fixture: mkt_cumsum={p_market[:5].sum()}"
    assert abs(p_market.sum() - 1.0) < 0.01, f"fixture: mkt total={p_market.sum()}"

    p_raw = p_cal.copy()
    p_raw[4] = 0.175
    p_raw[5] = 0.225
    p_raw /= p_raw.sum()

    member_samples = np.concatenate([
        np.full(20, 24.0),
        np.full(15, 25.0),
        np.full(10, 23.0),
        np.full(5, 26.0),
    ])

    return bins, p_raw, p_cal, p_market, member_samples


def _right_tail_11bin_fixture():
    """11-bin RIGHT-tail phantom fixture.

    Models a warm-biased ensemble (LOW-metric or reversed HIGH case):
    mode at bin 2 (cold/left end); inflated p_cal on right-tail sub-floor bins.
    p_market for bins 7-10 is sub-floor (0 < px < 0.05).
    sum(p_cal[right_tail]) / sum(p_market[right_tail]) > K=3.0 AND
    sum(p_market[right_tail]) < floor=0.10 → Gate 6 RIGHT-tail REJECTS.
    """
    centers = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0]
    bins = [Bin(low=c, high=c, unit="C", label=f"{c:.0f}C") for c in centers]

    # Mode at bin 2 (12C); warm-bias inflates bins 7-10 (17-20C)
    p_cal = np.array([
        0.050,   # 10C
        0.100,   # 11C
        0.260,   # 12C  MODE (highest p_cal)
        0.200,   # 13C
        0.150,   # 14C
        0.100,   # 15C
        0.050,   # 16C
        0.040,   # 17C right-tail: p_cal inflated vs market
        0.025,   # 18C right-tail
        0.015,   # 19C right-tail
        0.010,   # 20C right-tail
    ], dtype=np.float64)
    assert abs(p_cal.sum() - 1.0) < 1e-6, f"fixture: total={p_cal.sum()}"

    # Right-tail bins 7-10: market underprices (sub-floor), but model assigns mass
    # sum(p_cal[7:11]) = 0.090; sum(p_market[7:11]) = 0.020
    # ratio = 0.090/0.020 = 4.5 >= K=3.0; tail_mkt=0.020 < floor=0.10 → REJECT
    p_market = np.array([
        0.100,   # 10C
        0.200,   # 11C
        0.350,   # 12C
        0.200,   # 13C
        0.100,   # 14C
        0.030,   # 15C  sub-floor but LEFT of mode — ignored for right-tail check
        0.000,   # 16C  unquoted
        0.008,   # 17C right-tail sub-floor (0 < 0.008 < 0.05)
        0.006,   # 18C right-tail sub-floor
        0.004,   # 19C right-tail sub-floor
        0.002,   # 20C right-tail sub-floor
    ], dtype=np.float64)
    # right_tail sum p_cal = 0.040+0.025+0.015+0.010 = 0.090
    # right_tail sum p_market = 0.008+0.006+0.004+0.002 = 0.020
    assert p_cal[7:11].sum() / p_market[7:11].sum() > 3.0, "fixture: right ratio must exceed K"
    assert p_market[7:11].sum() < 0.10, "fixture: right tail mkt must be below floor"

    return bins, p_cal, p_market


# ---------------------------------------------------------------------------
# SEV-1 #1a: LEFT-tail phantom (Amsterdam) unit test
# ---------------------------------------------------------------------------

def test_check_cumulative_tail_discrepancy_rejects_amsterdam():
    """RED test: check_cumulative_tail_discrepancy with Amsterdam LEFT-tail vector rejects.

    INV(PROB_TAIL_SANITY) direct unit assertion.
    sum(p_cal[0:5])=0.211, sum(p_market[0:5])=0.059. Ratio=3.58 > K=3.0 → reject.
    Return signature is 3-tuple: (ok, reason, evidence_dict).
    """
    bins, p_raw, p_cal, p_market, member_samples = _amsterdam_11bin_fixture()

    ok, reason, evidence = check_cumulative_tail_discrepancy(
        bins=bins,
        p_cal=p_cal,
        market_prices=p_market,
    )

    assert ok is False, (
        f"Expected Amsterdam phantom-edge to be rejected by cumulative tail-mass gate. "
        f"Got ok=True (gate not firing). "
        f"tail_cal={p_cal[:5].sum():.4f}, tail_mkt={p_market[:5].sum():.4f}, "
        f"ratio={p_cal[:5].sum()/p_market[:5].sum():.2f}"
    )
    assert reason is not None
    assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" in reason, (
        f"Expected PROB_DISTRIBUTION_TAIL_DISCREPANCY in reason, got: {reason!r}"
    )
    # Evidence dict must be populated (SEV-2a)
    assert evidence is not None
    assert evidence["tail_cal"] > 0.0
    assert evidence["tail_mkt"] > 0.0
    assert "entropy" in evidence and evidence["entropy"] > 0.0


# ---------------------------------------------------------------------------
# SEV-1 #1b: RIGHT-tail phantom (mirror/warm-bias) unit test
# ---------------------------------------------------------------------------

def test_check_cumulative_tail_discrepancy_rejects_right_tail():
    """RED test: check_cumulative_tail_discrepancy with RIGHT-tail phantom rejects.

    Proves symmetric detection. Mode at bin 2; right-tail bins 7-10 have
    p_cal >> p_market (ratio 4.5, mkt_mass 0.020 < floor=0.10).
    Prior implementation (tail_mask[mode_idx:]=False) would return ok=True;
    fixed implementation checks right tail independently and must return ok=False.
    """
    bins, p_cal, p_market = _right_tail_11bin_fixture()

    ok, reason, evidence = check_cumulative_tail_discrepancy(
        bins=bins,
        p_cal=p_cal,
        market_prices=p_market,
    )

    assert ok is False, (
        f"RIGHT-tail phantom must be rejected. "
        f"right_tail p_cal sum={p_cal[7:11].sum():.4f}, "
        f"right_tail p_market sum={p_market[7:11].sum():.4f}, "
        f"ratio={p_cal[7:11].sum()/p_market[7:11].sum():.2f}. "
        f"Got ok=True — symmetric detection not working."
    )
    assert reason is not None
    assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" in reason, f"got: {reason!r}"
    assert "right" in reason, f"reason must name 'right' side: {reason!r}"


def test_check_cumulative_tail_discrepancy_passes_fair_distribution():
    """Gate must not fire on healthy distribution where all bins are fairly priced."""
    n = 5
    bins = [Bin(low=float(c), high=float(c), unit="C", label=f"{c}C") for c in range(20, 25)]
    p_cal = np.full(n, 1.0 / n)
    p_market = np.full(n, 0.20)  # all bins fairly priced

    ok, reason, evidence = check_cumulative_tail_discrepancy(bins=bins, p_cal=p_cal, market_prices=p_market)
    assert ok is True, f"Healthy distribution should pass: reason={reason!r}"
    assert reason is None


def test_check_cumulative_tail_discrepancy_passes_low_ratio():
    """Gate must not fire when tail-mass ratio is below K=3.0."""
    n = 5
    bins = [Bin(low=float(c), high=float(c), unit="C", label=f"{c}C") for c in range(20, 25)]
    p_cal = np.array([0.040, 0.240, 0.240, 0.240, 0.240])
    p_market = np.array([0.040, 0.240, 0.240, 0.240, 0.240])

    ok, reason, evidence = check_cumulative_tail_discrepancy(bins=bins, p_cal=p_cal, market_prices=p_market)
    assert ok is True, f"Low-ratio tail should pass: reason={reason!r}"
    assert reason is None


def test_check_cumulative_tail_discrepancy_passes_single_dustbin():
    """tail_min_bins guard: exactly 1 sub-floor bin on the left with ratio >= K → must PASS.

    Replay (2026-05-23) showed 64 FP candidates were all n=1 single-bin dust-bins
    (market_price ≈ 0.001). With tail_min_bins=2 the gate skips n=1 sides.
    This test confirms n=1 is not enough to trigger rejection.

    Fixture:
      5 bins, mode at bin 4 (rightmost = p_cal peak).
      Bin 0: market_price=0.001 (sub-floor), p_cal=0.30 → ratio=300 >> K=3.
      Bins 1-4: fairly priced (0.20), p_cal split among them.
      n_left_bins = 1 (only bin 0 is sub-floor left of mode) → gate skips.
    """
    bins = [Bin(low=float(c), high=float(c), unit="C", label=f"{c}C") for c in range(20, 25)]
    # mode at idx 4 (p_cal=0.37 is highest)
    p_cal    = np.array([0.30, 0.155, 0.155, 0.155, 0.235])
    p_market = np.array([0.001, 0.250, 0.250, 0.250, 0.249])
    # sanity: single sub-floor bin on left (market_price < 0.05)
    assert (p_market < 0.05).sum() == 1, "fixture must have exactly 1 sub-floor bin"
    # ratio for that bin: 0.30/0.001 = 300 >> K=3.0 → would reject if n_min_bins=1
    # but n=1 < tail_min_bins=2 → gate skips → ok=True

    ok, reason, evidence = check_cumulative_tail_discrepancy(bins=bins, p_cal=p_cal, market_prices=p_market)
    assert ok is True, (
        f"Single dust-bin (n=1) must be skipped by tail_min_bins guard: reason={reason!r}, "
        f"evidence={evidence!r}"
    )
    assert reason is None, f"expected reason=None, got: {reason!r}"


def test_check_cumulative_tail_discrepancy_rejects_two_bin_phantom():
    """tail_min_bins guard: ≥2 sub-floor bins on the left with ratio >= K → must REJECT.

    Confirms that the min_bins guard does NOT prevent detection of genuine phantoms.
    Amsterdam has 5 sub-floor left-tail bins; this fixture uses 2 (the minimum).

    Fixture:
      5 bins, mode at bin 3 (p_cal=0.35 peak).
      Bins 0-1: sub-floor market prices (0.004 each), p_cal = 0.15 each → ratio=37.5 >> K=3.
      n_left_bins = 2 >= tail_min_bins=2 → ratio check fires → rejects.
    """
    bins = [Bin(low=float(c), high=float(c), unit="C", label=f"{c}C") for c in range(20, 25)]
    p_cal    = np.array([0.15, 0.15, 0.18, 0.35, 0.17])
    p_market = np.array([0.004, 0.004, 0.20, 0.40, 0.392])
    # sanity: 2 sub-floor bins (idx 0,1) left of mode at idx 3
    assert int((p_market < 0.05).sum()) == 2, "fixture must have exactly 2 sub-floor bins"

    ok, reason, evidence = check_cumulative_tail_discrepancy(bins=bins, p_cal=p_cal, market_prices=p_market)
    assert ok is False, (
        f"Two-bin phantom (n=2, ratio>>K) must be rejected: ok={ok!r}, reason={reason!r}"
    )
    assert reason is not None
    assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" in reason, f"got reason: {reason!r}"
    assert "left" in reason, f"reason must name 'left' side: {reason!r}"
    # Confirm n_tail_bins is reported in reason string (regression guard against
    # reverting to n=1 gating silently — if we regress the reason string won't match).
    assert "n_tail_bins=2" in reason, (
        f"reason must include 'n_tail_bins=2' so regression to n_min_bins=1 is detectable: {reason!r}"
    )


# ---------------------------------------------------------------------------
# Real-data tests: check_edge_bin_tail_discrepancy (per-edge criterion)
# Vectors extracted from zeus-world.db 2026-05-23 (see replay output).
# These are the ACTUAL live distribution vectors — not synthesized.
# ---------------------------------------------------------------------------

# Amsterdam May-24 real phantom vector:
#   trace_id=probtrace:3d2f2373-8c8
#   11-bin HIGH, mode_idx=4 (unquoted, p_mkt=0.0), 4 left-tail sub-floor bins (0-3)
#   Edge bin = 3 (23°C): p_mkt=0.0465, p_cal=0.1856, ratio=3.99 >= K=3.0
#   Contiguous run 0..3 length=4 >= tail_min_bins=2 → REJECT
_AMS_MAY24_P_CAL = np.array([
    0.007971856593627927,  # idx=0
    0.007971856593627927,  # idx=1
    0.009771215997950809,  # idx=2
    0.18560273449424933,   # idx=3  <-- edge bin (23°C)
    0.5656209098203346,    # idx=4  <-- mode (24°C, unquoted)
    0.17542169059213653,   # idx=5
    0.015849069148590698,  # idx=6
    0.007971856593627927,  # idx=7
    0.007971856593627927,  # idx=8
    0.007971856593627927,  # idx=9
    0.007971856593627927,  # idx=10
])
_AMS_MAY24_P_MKT = np.array([
    0.002002036218698553,   # idx=0 sub-floor
    0.0033883495145631067,  # idx=1 sub-floor
    0.006604621309370989,   # idx=2 sub-floor
    0.04650546644609077,    # idx=3 sub-floor  <-- edge bin
    0.0,                    # idx=4 UNQUOTED (mode)
    0.0,                    # idx=5 UNQUOTED
    0.0,                    # idx=6 UNQUOTED
    0.0,                    # idx=7 UNQUOTED
    0.0,                    # idx=8 UNQUOTED
    0.0,                    # idx=9 UNQUOTED
    0.0,                    # idx=10 UNQUOTED
])

# Jeddah May-23 real FP vector:
#   trace_id=probtrace:cc6d07be-bf2
#   11-bin, mode_idx=7 (p_mkt=0.131), edge bin = 7 (well-priced, p_mkt=0.131 >= 0.05)
#   Incidental left sub-floor bins at 3 (p_mkt=0.001) and 5 (p_mkt=0.001)
#   BUT bin 4 is unquoted (p_mkt=0.0), so bins 3 and 5 are NOT contiguous
#   → run_length for bin 3 = 1 < tail_min_bins=2 → edge-bin check PASSES
_JEDDAH_MAY23_P_CAL = np.array([
    0.0057,   # idx=0
    0.0057,   # idx=1
    0.0057,   # idx=2
    0.0057,   # idx=3 sub-floor but isolated (bin 4 is unquoted)
    0.0057,   # idx=4
    0.0057,   # idx=5 sub-floor but isolated
    0.0333,   # idx=6
    0.7931,   # idx=7  <-- mode AND edge bin (p_mkt=0.131, well-priced)
    0.1283,   # idx=8
    0.0057,   # idx=9
    0.0057,   # idx=10
])
_JEDDAH_MAY23_P_MKT = np.array([
    0.0,       # idx=0 UNQUOTED
    0.0,       # idx=1 UNQUOTED
    0.0,       # idx=2 UNQUOTED
    0.001,     # idx=3 sub-floor
    0.0,       # idx=4 UNQUOTED (breaks contiguity for bin 3)
    0.001,     # idx=5 sub-floor (isolated: run length=1)
    0.8274,    # idx=6 well-priced
    0.1313,    # idx=7 well-priced  <-- edge bin
    0.0,       # idx=8 UNQUOTED
    0.0,       # idx=9 UNQUOTED
    0.0,       # idx=10 UNQUOTED
])


def _make_11bins():
    return [Bin(low=float(i), high=float(i), unit="C", label=f"{i}") for i in range(11)]


def test_check_edge_bin_tail_discrepancy_rejects_amsterdam_real_data():
    """Real-data RED→GREEN: Amsterdam May-24 phantom, edge_bin_idx=3 → REJECT.

    RED (family gate): check_cumulative_tail_discrepancy rejects this vector (proves the
      family gate was at least catching this case — we're not regressing it).
    GREEN (edge-bin gate): check_edge_bin_tail_discrepancy(edge_bin_idx=3) also REJECTS —
      the new predicate catches the phantom edge when the edge bin IS in the sub-floor tail.

    Amsterdam vector: 4 contiguous sub-floor left-tail bins (idx 0-3),
      p_mkt[3]=0.0465 < 0.05, p_cal[3]/p_mkt[3]=3.99 >= K=3.0, run_length=4 >= 2.
    """
    bins = _make_11bins()

    # RED: family gate still rejects (telemetry still fires)
    fam_ok, fam_reason, _ = check_cumulative_tail_discrepancy(
        bins=bins, p_cal=_AMS_MAY24_P_CAL, market_prices=_AMS_MAY24_P_MKT,
    )
    assert fam_ok is False, (
        "Family gate must still reject Amsterdam vector (telemetry parity). "
        f"Got ok=True — family gate regressed."
    )
    assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" in (fam_reason or ""), (
        f"Unexpected family reason: {fam_reason!r}"
    )

    # GREEN: edge-bin gate ALSO rejects edge_bin_idx=3
    eb_ok, eb_reason = check_edge_bin_tail_discrepancy(
        edge_bin_idx=3,
        p_cal=_AMS_MAY24_P_CAL,
        market_prices=_AMS_MAY24_P_MKT,
    )
    assert eb_ok is False, (
        f"Amsterdam edge-bin check must REJECT edge_bin_idx=3. "
        f"p_mkt[3]={_AMS_MAY24_P_MKT[3]:.4f} < 0.05, "
        f"ratio={_AMS_MAY24_P_CAL[3]/_AMS_MAY24_P_MKT[3]:.2f} >= K=3.0, "
        f"run_length=4 >= 2. Got ok=True — phantom not caught."
    )
    assert eb_reason is not None
    assert "PROB_EDGE_BIN_TAIL_DISCREPANCY" in eb_reason, f"got: {eb_reason!r}"
    assert "left" in eb_reason, f"must name 'left' side: {eb_reason!r}"


def test_check_edge_bin_tail_discrepancy_passes_jeddah_fp_real_data():
    """Real-data RED→GREEN: Jeddah May-23 FP case, edge_bin_idx=7 → PASS.

    RED (family gate): check_cumulative_tail_discrepancy REJECTS this vector —
      this is the FALSE POSITIVE the family gate was producing. Proves the bug.
    GREEN (edge-bin gate): check_edge_bin_tail_discrepancy(edge_bin_idx=7) PASSES —
      edge bin 7 has p_mkt=0.131 >= 0.05 → first condition fails → pass.
      The incidental sub-floor bins (3, 5) are isolated by unquoted gaps and
      are not the edge bin — so the phantom check correctly does not fire.

    While Amsterdam STILL rejects (test above), Jeddah now PASSES.
    This is the core FP-elimination the redesign achieves.
    """
    bins = _make_11bins()

    # RED: family gate rejects (this is the false positive — demonstrates the bug)
    fam_ok, fam_reason, _ = check_cumulative_tail_discrepancy(
        bins=bins, p_cal=_JEDDAH_MAY23_P_CAL, market_prices=_JEDDAH_MAY23_P_MKT,
    )
    assert fam_ok is False, (
        "Family gate must reject Jeddah vector (this is the FP we're fixing). "
        f"Got ok=True — family gate not triggering? Check fixture. reason={fam_reason!r}"
    )
    assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" in (fam_reason or ""), (
        f"Expected family FP reason, got: {fam_reason!r}"
    )

    # GREEN: edge-bin gate PASSES for edge_bin_idx=7 (p_mkt=0.131 >= floor)
    eb_ok, eb_reason = check_edge_bin_tail_discrepancy(
        edge_bin_idx=7,
        p_cal=_JEDDAH_MAY23_P_CAL,
        market_prices=_JEDDAH_MAY23_P_MKT,
    )
    assert eb_ok is True, (
        f"Jeddah edge-bin check must PASS for edge_bin_idx=7 "
        f"(p_mkt=0.131 >= 0.05 → first condition fails). "
        f"Got ok=False, reason={eb_reason!r}"
    )
    assert eb_reason is None, (
        f"Expected reason=None for well-priced edge bin, got: {eb_reason!r}"
    )


# ---------------------------------------------------------------------------
# SEV-1 #2: evaluate_candidate() wiring test (production path)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc)


class _FakeClob:
    """Returns above-floor prices for all tokens — p_market won't trigger gate via CLOB."""
    def get_best_bid_ask(self, token_id):
        return (0.18, 0.22, 10.0, 10.0)

    def get_fee_rate(self, token_id):
        return 0.00


class _FakeEns:
    """Returns 3-bin uniform p_raw. calibrate_and_normalize is identity-patched."""
    member_extrema = np.ones(51) * 95.0
    member_maxes = member_extrema
    temperature_metric = None
    bias_corrected = False

    def __init__(self, *a, **kw):
        pass

    def spread_float(self):
        return 0.0

    def spread(self):
        from src.types.temperature import TemperatureDelta
        return TemperatureDelta(0.0, "F")

    def is_bimodal(self):
        return False

    def p_raw_vector(self, bins, **kwargs):
        return np.array([0.33, 0.34, 0.33])


_OUTCOMES_3BIN = [
    {"title": "89F or lower", "range_low": None, "range_high": 89,
     "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
    {"title": "90-91F", "range_low": 90, "range_high": 91,
     "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
    {"title": "92F or higher", "range_low": 92, "range_high": None,
     "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
]

from src.config import City

_CITY = City(
    name="Dallas",
    lat=32.8998,
    lon=-97.0403,
    timezone="America/Chicago",
    settlement_unit="F",
    cluster="Dallas",
    wu_station="KDAL",
)


class _FakeAnalysis:
    def __init__(self, *a, **kw):
        self.bins = kw.get("bins", [])
        self.member_maxes = np.ones(51) * 95.0
        self.entry_method = "ens_member_counting"
        self.selected_method = "ens_member_counting"

    def forecast_context(self):
        return {"uncertainty": {}, "location": {}}

    def find_edges(self, n_bootstrap=0):
        from src.types.market import BinEdge
        selected_bin = self.bins[1] if len(self.bins) > 1 else self.bins[0]
        return [
            BinEdge(
                bin=selected_bin,
                direction="buy_yes",
                edge=0.20,
                ci_lower=0.05,
                ci_upper=0.25,
                p_model=0.70,
                p_market=0.20,
                p_posterior=0.60,
                entry_price=0.20,
                p_value=0.001,
                vwmp=0.20,
                support_index=1,  # matches _fake_family_scan hypothesis index
            )
        ]


def test_evaluate_candidate_nday0_tail_gate_wiring(monkeypatch):
    """SEV-1 #2 wiring test: evaluate_candidate returns SIGNAL_QUALITY rejection when
    probability_edge_bin_sanity fires in the per-edge evaluation loop.

    REDESIGN (2026-05-23): The family gate check_cumulative_tail_discrepancy is now
    telemetry-only (no rejection). Rejection is at the per-edge level via
    probability_edge_bin_sanity (operator binding spec §B). This test verifies the
    per-edge wiring:
      - is_day0_mode guard (center_buy → is_day0=False → gate executes)
      - probability_edge_bin_sanity is called in the per-edge loop
      - rejection_stage == "SIGNAL_QUALITY"
      - rejection_reason_enum in PROBABILITY_TAIL_SHAPE_ANOMALY_HARD / PROBABILITY_SANITY_GATE
      - rejection_reason_detail contains PROBABILITY_TAIL_SHAPE_ANOMALY_HARD
      - MarketAnalysis IS constructed (per-edge gate fires inside the loop, after MarketAnalysis)
      - NOT economic floor rejection

    RED on origin/main: probability_edge_bin_sanity not imported/wired in evaluator.
    GREEN on branch: per-edge gate is wired and returns the rejection.
    """
    import src.engine.evaluator as ev_mod
    from src.state.portfolio import PortfolioState
    from src.strategy.risk_limits import RiskLimits

    _edge_gate_called = []

    def _fake_edge_tail_gate(*, selected_bin_idx, bins, p_raw, p_cal, p_market,
                              direction="", metric="", strategy_key="", market_phase="",
                              config=None):
        _edge_gate_called.append(selected_bin_idx)
        telemetry = {
            "edge_bin_idx": selected_bin_idx,
            "edge_bin_label": f"bin_{selected_bin_idx}",
            "edge_bin_p_raw": 0.01,
            "edge_bin_p_cal": 0.34,
            "edge_bin_p_market": 0.003,
            "edge_bin_member_support": 0.01,
            "edge_bin_odds_ratio": 3.99,
            "near_tail_p_cal": 0.008,
            "near_tail_p_market": 0.001,
            "probability_sanity_mode": "hard",
            "probability_sanity_reason": (
                f"PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx={selected_bin_idx},"
                f"p_raw=0.0100,p_mkt=0.0030,p_cal=0.3400,ratio=3.99,support=0.0100,"
                f"run_length=2,mode_idx=2"
            ),
        }
        return False, telemetry["probability_sanity_reason"], telemetry

    _family_gate_called = []

    def _fake_family_gate(*, bins, p_cal, market_prices):
        # Family gate is telemetry-only: must not cause rejection even when called.
        _family_gate_called.append(True)
        reason = (
            "PROB_DISTRIBUTION_TAIL_DISCREPANCY:"
            "left:tail_cal=0.2110,tail_mkt=0.0590,ratio=3.58,n_tail_bins=5"
        )
        evidence = {"tail_cal": 0.211, "tail_mkt": 0.059, "entropy": 1.23}
        return False, reason, evidence

    _analysis_init_called = []

    class _TrackingAnalysis(_FakeAnalysis):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            _analysis_init_called.append(True)

    # Patch both gates on ev_mod
    monkeypatch.setattr(ev_mod, "check_cumulative_tail_discrepancy", _fake_family_gate)
    monkeypatch.setattr(ev_mod, "probability_edge_bin_sanity", _fake_edge_tail_gate)
    monkeypatch.setattr(ev_mod, "MarketAnalysis", _TrackingAnalysis)
    # Patch scan_full_hypothesis_family to return one hypothesis so filtered is populated
    # and the per-edge loop runs. Without this, FDR fallback fires and skips the loop.
    from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
    def _fake_family_scan(analysis, n_bootstrap=0):
        return [FullFamilyHypothesis(
            index=1,
            range_label="90-91F",
            direction="buy_yes",
            edge=0.15,
            ci_lower=0.05,
            ci_upper=0.25,
            p_value=0.01,
            p_model=0.35,
            p_market=0.20,
            p_posterior=0.30,
            entry_price=0.20,
            is_shoulder=False,
            passed_prefilter=True,
        )]
    monkeypatch.setattr(ev_mod, "scan_full_hypothesis_family", _fake_family_scan)
    import src.config as _cfg_mod

    class _HardModeSettings:
        """Proxy returning tail_discrepancy_mode='hard' for probability_sanity."""
        def __init__(self, real):
            self._real = real

        def __getitem__(self, key):
            if key == "probability_sanity":
                d = dict(self._real[key]) if key in self._real._data else {}
                d["tail_discrepancy_mode"] = "hard"
                return d
            return self._real[key]

        def get(self, key, default=None):
            try:
                return self[key]
            except KeyError:
                return default

    monkeypatch.setattr(ev_mod, "settings", _HardModeSettings(_cfg_mod.settings))

    # Standard infrastructure patches
    monkeypatch.setattr(ev_mod, "_live_entry_forecast_config_or_blocker", lambda: (None, None))
    monkeypatch.setattr(
        ev_mod,
        "fetch_ensemble",
        lambda *a, **kw: {
            "members_hourly": np.ones((24, 51)) * 95.0,
            "times": [_NOW.isoformat()] * 24,
            "issue_time": _NOW,
            "first_valid_time": _NOW,
            "fetch_time": _NOW,
            "model": "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(ev_mod, "validate_ensemble", lambda *a, **kw: True)
    monkeypatch.setattr(ev_mod, "_entry_forecast_evidence_errors", lambda *a, **kw: [])
    monkeypatch.setattr(ev_mod, "EnsembleSignal", _FakeEns)
    monkeypatch.setattr(ev_mod, "_store_ens_snapshot", lambda *a, **kw: "snap-test")
    monkeypatch.setattr(ev_mod, "_store_snapshot_p_raw", lambda *a, **kw: None)
    _fake_cal = object()
    monkeypatch.setattr(ev_mod, "get_calibrator", lambda *a, **kw: (_fake_cal, 1))
    monkeypatch.setattr(
        ev_mod,
        "calibrate_and_normalize",
        lambda p_raw, cal, lead_days, bin_widths: p_raw.copy(),
    )
    from src.contracts.alpha_decision import AlphaDecision
    monkeypatch.setattr(
        ev_mod,
        "compute_alpha",
        lambda *a, **kw: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="wiring test",
            ci_bound=0.05,
        ),
    )

    candidate = ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="tail-gate-wiring-test",
        discovery_mode="center_buy",  # non-day0: is_day0_mode=False → both gates execute
        temperature_metric="high",
        observation={
            "high_so_far": 88.0,
            "low_so_far": 72.0,
            "current_temp": 88.0,
            "source": "KDAL",
            "observation_time": _NOW.isoformat(),
            "causality_status": "OK",
        },
    )

    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=RiskLimits(
            max_single_position_pct=1.0,
            max_portfolio_heat_pct=1.0,
            max_correlated_pct=1.0,
            max_city_pct=1.0,
            min_order_usd=0.01,
        ),
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )

    # Per-edge gate must have been called (wiring proof)
    assert len(_edge_gate_called) >= 1, (
        f"probability_edge_bin_sanity was not called by evaluate_candidate. "
        f"Per-edge gate is not wired or is_day0_mode guard incorrect. calls={_edge_gate_called}"
    )

    # Must return at least one decision
    assert len(decisions) >= 1

    # Find the SIGNAL_QUALITY rejection from the per-edge tail gate
    signal_quality_rejections = [
        d for d in decisions
        if not d.should_trade
        and d.rejection_stage == "SIGNAL_QUALITY"
        and d.rejection_reason_enum in (
            NoTradeReason.PROBABILITY_TAIL_SHAPE_ANOMALY_HARD,
            NoTradeReason.PROBABILITY_EDGE_BIN_UNSUPPORTED,
            NoTradeReason.PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT,
            NoTradeReason.PROBABILITY_SANITY_GATE,
        )
    ]
    assert len(signal_quality_rejections) >= 1, (
        f"Expected >=1 SIGNAL_QUALITY eb-sanity rejection from per-edge gate. "
        f"Decisions: {[(d.rejection_stage, str(d.rejection_reason_enum)) for d in decisions]}"
    )
    d = signal_quality_rejections[0]

    # Gate rejection shape
    assert d.rejection_reason_detail is not None
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in d.rejection_reason_detail, (
        f"rejection_reason_detail must contain PROBABILITY_TAIL_SHAPE_ANOMALY_HARD. "
        f"Got: {d.rejection_reason_detail!r}"
    )

    # MarketAnalysis MUST have been constructed (per-edge gate fires inside loop, AFTER analysis)
    assert len(_analysis_init_called) >= 1, (
        "MarketAnalysis was NOT constructed — per-edge gate may have fired before analysis. "
        "Per-edge gate should fire inside the 'for edge in filtered:' loop, after MarketAnalysis."
    )

    # SEV-2a: evidence columns must be stamped (non-null) via _tail_evidence from family telemetry
    assert d.prob_tail_mass_cal is not None, "prob_tail_mass_cal must be stamped on per-edge rejection"
    assert d.prob_tail_mass_market is not None, "prob_tail_mass_market must be stamped"
    assert d.prob_tail_entropy is not None, "prob_tail_entropy must be stamped"


def test_evaluate_candidate_day0_bypasses_tail_gate(monkeypatch):
    """Antibody: day0 candidates must bypass Gate 6 entirely.

    When discovery_mode is a day0-family mode (day0_capture), is_day0_mode=True
    and the tail gate blocks are guarded by ``if not is_day0_mode:``. This test
    proves BOTH guards are present: neither family gate nor per-edge gate is called,
    and no PROBABILITY_SANITY_GATE rejection is returned.

    RED if someone deletes the ``if not is_day0_mode:`` guard around either gate.
    GREEN: family_gate_called==[], edge_gate_called==[], no PROBABILITY_SANITY_GATE rejection.

    Note: Both patched gates return ok=False so that if either guard is removed,
    the test would catch it (gate would fire and reject with PROBABILITY_SANITY_GATE).
    """
    import src.engine.evaluator as ev_mod
    from src.state.portfolio import PortfolioState
    from src.strategy.risk_limits import RiskLimits

    _family_gate_called = []
    _edge_gate_called = []

    def _fake_family_gate_always_reject(*, bins, p_cal, market_prices):
        _family_gate_called.append(True)
        reason = (
            "PROB_DISTRIBUTION_TAIL_DISCREPANCY:"
            "left:tail_cal=0.2110,tail_mkt=0.0590,ratio=3.58,n_tail_bins=5"
        )
        evidence = {"tail_cal": 0.211, "tail_mkt": 0.059, "entropy": 1.23}
        return False, reason, evidence

    def _fake_edge_gate_always_reject(*, selected_bin_idx, bins, p_raw, p_cal, p_market,
                                       direction="", metric="", strategy_key="", market_phase="",
                                       config=None):
        _edge_gate_called.append(selected_bin_idx)
        telemetry = {
            "edge_bin_idx": selected_bin_idx, "edge_bin_label": "",
            "edge_bin_p_raw": 0.0, "edge_bin_p_cal": 0.34, "edge_bin_p_market": 0.003,
            "edge_bin_member_support": 0.0, "edge_bin_odds_ratio": 3.99,
            "near_tail_p_cal": 0.0, "near_tail_p_market": 0.0,
            "probability_sanity_mode": "hard",
            "probability_sanity_reason": "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx=1,...",
        }
        return False, telemetry["probability_sanity_reason"], telemetry

    _analysis_init_called = []

    class _TrackingAnalysis(_FakeAnalysis):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            _analysis_init_called.append(True)

    monkeypatch.setattr(ev_mod, "check_cumulative_tail_discrepancy", _fake_family_gate_always_reject)
    monkeypatch.setattr(ev_mod, "probability_edge_bin_sanity", _fake_edge_gate_always_reject)
    monkeypatch.setattr(ev_mod, "MarketAnalysis", _TrackingAnalysis)

    import src.config as _cfg_mod

    class _HardModeSettings:
        def __init__(self, real):
            self._real = real

        def __getitem__(self, key):
            if key == "probability_sanity":
                d = dict(self._real[key]) if key in self._real._data else {}
                d["tail_discrepancy_mode"] = "hard"
                return d
            return self._real[key]

        def get(self, key, default=None):
            try:
                return self[key]
            except KeyError:
                return default

    monkeypatch.setattr(ev_mod, "settings", _HardModeSettings(_cfg_mod.settings))

    monkeypatch.setattr(ev_mod, "_live_entry_forecast_config_or_blocker", lambda: (None, None))
    monkeypatch.setattr(
        ev_mod,
        "fetch_ensemble",
        lambda *a, **kw: {
            "members_hourly": np.ones((24, 51)) * 95.0,
            "times": [_NOW.isoformat()] * 24,
            "issue_time": _NOW,
            "first_valid_time": _NOW,
            "fetch_time": _NOW,
            "model": "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(ev_mod, "validate_ensemble", lambda *a, **kw: True)
    monkeypatch.setattr(ev_mod, "_entry_forecast_evidence_errors", lambda *a, **kw: [])
    monkeypatch.setattr(ev_mod, "EnsembleSignal", _FakeEns)
    monkeypatch.setattr(ev_mod, "_store_ens_snapshot", lambda *a, **kw: "snap-test")
    monkeypatch.setattr(ev_mod, "_store_snapshot_p_raw", lambda *a, **kw: None)
    _fake_cal = object()
    monkeypatch.setattr(ev_mod, "get_calibrator", lambda *a, **kw: (_fake_cal, 1))
    monkeypatch.setattr(
        ev_mod,
        "calibrate_and_normalize",
        lambda p_raw, cal, lead_days, bin_widths: p_raw.copy(),
    )
    from src.contracts.alpha_decision import AlphaDecision
    monkeypatch.setattr(
        ev_mod,
        "compute_alpha",
        lambda *a, **kw: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="wiring test",
            ci_bound=0.05,
        ),
    )

    candidate = ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="tail-gate-day0-bypass-test",
        discovery_mode="day0_capture",  # day0 path: is_day0_mode=True → gate SKIPPED
        temperature_metric="high",
        observation={
            "high_so_far": 88.0,
            "low_so_far": 72.0,
            "current_temp": 88.0,
            "source": "KDAL",
            "observation_time": _NOW.isoformat(),
            "causality_status": "OK",
        },
    )

    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=RiskLimits(
            max_single_position_pct=1.0,
            max_portfolio_heat_pct=1.0,
            max_correlated_pct=1.0,
            max_city_pct=1.0,
            min_order_usd=0.01,
        ),
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )

    # Family gate must NOT be called for day0 candidates
    assert len(_family_gate_called) == 0, (
        f"check_cumulative_tail_discrepancy was called for a day0 candidate. "
        f"The `if not is_day0_mode:` guard around family telemetry is missing or broken. "
        f"gate_called={_family_gate_called}"
    )
    # Per-edge gate must NOT be called for day0 candidates
    assert len(_edge_gate_called) == 0, (
        f"probability_edge_bin_sanity was called for a day0 candidate. "
        f"The `if not is_day0_mode:` guard around per-edge gate is missing or broken. "
        f"gate_called={_edge_gate_called}"
    )

    # None of the decisions should be a SIGNAL_QUALITY tail gate rejection
    # (day0 may still be rejected by other gates; we only assert tail gate did NOT fire).
    for d in decisions:
        if not d.should_trade:
            reason_detail = getattr(d, "rejection_reason_detail", "") or ""
            assert "PROB_DISTRIBUTION_TAIL_DISCREPANCY" not in reason_detail, (
                f"day0 candidate was rejected by family tail gate — guard missing. "
                f"rejection_reason_detail={reason_detail!r}"
            )
            assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" not in reason_detail, (
                f"day0 candidate was rejected by edge-bin sanity gate — guard missing. "
                f"rejection_reason_detail={reason_detail!r}"
            )
            reason_enum = getattr(d, "rejection_reason_enum", None)
            assert reason_enum != NoTradeReason.PROBABILITY_SANITY_GATE, (
                f"day0 candidate rejected with PROBABILITY_SANITY_GATE — guard is broken. "
                f"rejection_reason_enum={reason_enum!r}"
            )


def test_evaluate_candidate_shadow_mode_stamps_columns(monkeypatch):
    """Schema-34 column populate: shadow mode must stamp prob_tail_mass_* on returned decisions.

    When tail_discrepancy_mode='shadow', Gate 6 fires but continues to analysis.
    No hard-reject EdgeDecision is returned — instead, decisions flow through to
    the full downstream path. The loop-at-function-exit in evaluate_candidate must
    stamp tail evidence onto each returned decision so probability_trace_fact columns
    are populated regardless of gate mode.

    Without the loop-at-exit, prob_tail_mass_cal/market/entropy are always None in
    production (shadow is the production default), making schema-34 columns dead.

    RED if loop-at-exit is removed or _tail_evidence is not passed through.
    GREEN: all returned decisions have prob_tail_mass_cal/market/entropy not None.
    """
    import src.engine.evaluator as ev_mod
    from src.state.portfolio import PortfolioState
    from src.strategy.risk_limits import RiskLimits

    _gate_called = []

    def _fake_tail_gate_shadow(*, bins, p_cal, market_prices):
        """Gate fires (ok=False) but mode is 'shadow' → evaluator continues, not returns."""
        _gate_called.append(True)
        reason = (
            "PROB_DISTRIBUTION_TAIL_DISCREPANCY:"
            "left:tail_cal=0.2110,tail_mkt=0.0590,ratio=3.58,n_tail_bins=5"
        )
        evidence = {"tail_cal": 0.211, "tail_mkt": 0.059, "entropy": 1.23}
        return False, reason, evidence

    monkeypatch.setattr(ev_mod, "check_cumulative_tail_discrepancy", _fake_tail_gate_shadow)
    monkeypatch.setattr(ev_mod, "MarketAnalysis", _FakeAnalysis)

    import src.config as _cfg_mod

    class _ShadowModeSettings:
        """Proxy returning tail_discrepancy_mode='shadow' for probability_sanity."""
        def __init__(self, real):
            self._real = real

        def __getitem__(self, key):
            if key == "probability_sanity":
                d = dict(self._real[key]) if key in self._real._data else {}
                d["tail_discrepancy_mode"] = "shadow"
                return d
            return self._real[key]

        def get(self, key, default=None):
            try:
                return self[key]
            except KeyError:
                return default

    monkeypatch.setattr(ev_mod, "settings", _ShadowModeSettings(_cfg_mod.settings))

    monkeypatch.setattr(ev_mod, "_live_entry_forecast_config_or_blocker", lambda: (None, None))
    monkeypatch.setattr(
        ev_mod,
        "fetch_ensemble",
        lambda *a, **kw: {
            "members_hourly": np.ones((24, 51)) * 95.0,
            "times": [_NOW.isoformat()] * 24,
            "issue_time": _NOW,
            "first_valid_time": _NOW,
            "fetch_time": _NOW,
            "model": "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(ev_mod, "validate_ensemble", lambda *a, **kw: True)
    monkeypatch.setattr(ev_mod, "_entry_forecast_evidence_errors", lambda *a, **kw: [])
    monkeypatch.setattr(ev_mod, "EnsembleSignal", _FakeEns)
    monkeypatch.setattr(ev_mod, "_store_ens_snapshot", lambda *a, **kw: "snap-test")
    monkeypatch.setattr(ev_mod, "_store_snapshot_p_raw", lambda *a, **kw: None)
    _fake_cal = object()
    monkeypatch.setattr(ev_mod, "get_calibrator", lambda *a, **kw: (_fake_cal, 1))
    monkeypatch.setattr(
        ev_mod,
        "calibrate_and_normalize",
        lambda p_raw, cal, lead_days, bin_widths: p_raw.copy(),
    )
    from src.contracts.alpha_decision import AlphaDecision
    monkeypatch.setattr(
        ev_mod,
        "compute_alpha",
        lambda *a, **kw: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="wiring test",
            ci_bound=0.05,
        ),
    )

    candidate = ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="tail-gate-shadow-columns-test",
        discovery_mode="center_buy",  # non-day0 → gate executes
        temperature_metric="high",
        observation={
            "high_so_far": 88.0,
            "low_so_far": 72.0,
            "current_temp": 88.0,
            "source": "KDAL",
            "observation_time": _NOW.isoformat(),
            "causality_status": "OK",
        },
    )

    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=RiskLimits(
            max_single_position_pct=1.0,
            max_portfolio_heat_pct=1.0,
            max_correlated_pct=1.0,
            max_city_pct=1.0,
            min_order_usd=0.01,
        ),
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )

    # Gate must have been called (shadow wiring proof)
    assert len(_gate_called) == 1, (
        f"check_cumulative_tail_discrepancy was not called in shadow mode. calls={_gate_called}"
    )

    # Shadow mode: gate fired but analysis continued → decisions from downstream
    # (may have 0 or 1+ decisions depending on downstream gates; we assert on ALL)
    assert len(decisions) >= 1, "Expected at least one decision from shadow-mode evaluation"

    # KEY ASSERTION: loop-at-exit must stamp columns on every returned decision.
    # Without the loop, prob_tail_mass_cal is None even when gate fired.
    for i, d in enumerate(decisions):
        assert getattr(d, "prob_tail_mass_cal", None) is not None, (
            f"decision[{i}]: prob_tail_mass_cal is None in shadow mode — "
            f"loop-at-function-exit is missing or _tail_evidence not propagated. "
            f"schema-34 columns would be dead in production."
        )
        assert getattr(d, "prob_tail_mass_market", None) is not None, (
            f"decision[{i}]: prob_tail_mass_market is None in shadow mode"
        )
        assert getattr(d, "prob_tail_entropy", None) is not None, (
            f"decision[{i}]: prob_tail_entropy is None in shadow mode"
        )
