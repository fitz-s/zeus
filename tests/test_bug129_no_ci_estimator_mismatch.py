# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: BUG #129 (mandate-#2 SEV1) — docs/archive/2026-Q2/operations_historical/PROBABILITY_INTEGRITY_AUDIT_2026-06-02.md LEG 1.
#   The NO-direction CI lower bound (q_lcb) is computed by _bootstrap_bin_no using a DIFFERENT
#   probability estimator (random HISTORICAL Platt params per sample) than the point q_live
#   (current/MAP Platt). For high-q_no bins the historical-param distribution maps q_no systematically
#   higher, so the 5th-percentile q_no lands ABOVE the point. robust_trade_score's min(q_lcb, q_live)
#   then binds on the point term and the designed CI haircut is silently bypassed on the NO leg.
"""Relationship test for BUG #129: the NO bootstrap LCB must share the point's estimator.

Cross-module invariant (the antibody): for EVERY bin and direction,
    q_lcb (5th-percentile lower bound, restored to probability space) <= q_posterior (point).
A "lower bound" that exceeds the point is not a lower bound; it removes the designed CI haircut.

Reproduction strategy (deterministic): a calibrator whose MAP params map faithfully but whose
HISTORICAL bootstrap_params are systematically more negative in the intercept (C) — i.e. they map
p_yes lower / q_no higher. This is the exact dual-estimator mismatch the audit measured in live data
(historical-Platt C mean -0.493 < MAP C -0.470, amplified here so the ceiling skew is unambiguous).
"""

import numpy as np
import pytest

from src.strategy.market_analysis import MarketAnalysis
from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
from src.strategy.market_fusion import MODEL_ONLY_POSTERIOR_MODE
from src.calibration.platt import ExtendedPlattCalibrator, RAW_PROBABILITY_SPACE
from src.types import Bin


def _calibrator_with_qno_inflating_history() -> ExtendedPlattCalibrator:
    """MAP params map faithfully (identity logit, C=0); HISTORICAL params push q_no toward the ceiling.

    The point estimate uses (A=1, B=0, C=0). The NO bootstrap draws from bootstrap_params, which are
    set to (1, 0, -2.0): a strongly negative intercept that maps every p_yes lower, so 1-p_yes (q_no)
    is pinned near 1.0. For a high-q_no bin this lifts the 5th-percentile q_no ABOVE the MAP point —
    the BUG #129 inversion — reproduced deterministically, no RNG dependence on member spread.
    """
    cal = ExtendedPlattCalibrator()
    cal.fitted = True
    cal.input_space = RAW_PROBABILITY_SPACE
    cal.A, cal.B, cal.C = 1.0, 0.0, 0.0  # MAP / current fit — faithful point estimator
    cal.bootstrap_params = [(1.0, 0.0, -2.0)] * 200  # historical distribution — inflates q_no
    return cal


def _eleven_bin_high_qno_analysis(cal: ExtendedPlattCalibrator) -> tuple[MarketAnalysis, int]:
    """11-bin F market; members cluster in one 2°F bin so an empty low bin is HIGH-q_no (~0.99).

    This is the audit's inversion regime (q_no >= 0.99, 91% inversion in live data).
    Returns (analysis, held_bin_idx) where held_bin_idx is an empty high-q_no bin.
    """
    bins = [Bin(low=None, high=60, label="60°F or below", unit="F")]
    for lo in range(61, 79, 2):
        bins.append(Bin(low=lo, high=lo + 1, label=f"{lo}-{lo + 1}°F", unit="F"))
    bins.append(Bin(low=79, high=None, label="79°F or above", unit="F"))
    nb = len(bins)

    # Tight ensemble at ~69.5°F -> all mass in the "69-70°F" bin; every other bin is empty.
    member_maxes = np.array(
        [69.4, 69.5, 69.6, 69.45, 69.55, 69.5, 69.5, 69.6, 69.4, 69.55, 69.5, 69.5]
    )
    lead = 3.0

    def _contains(b: Bin, x: float) -> bool:
        lo = -1e9 if b.low is None else b.low
        hi = 1e9 if b.high is None else b.high
        return lo <= x <= hi

    measured = np.floor(member_maxes + 0.5)
    p_raw = np.array(
        [float(np.mean([_contains(b, m) for m in measured])) for b in bins]
    )
    # Point p_cal: MAP-calibrate each raw bin prob, then normalize (as upstream passes it in).
    p_cal = np.array(
        [cal.predict_for_bin(max(float(p_raw[i]), 1e-6), lead, bin_width=2.0) for i in range(nb)]
    )
    p_cal = p_cal / p_cal.sum()

    p_market = np.full(nb, 0.05)
    p_market[int(np.argmax(p_raw))] = 0.90
    p_market_no = np.full(nb, 0.95)

    ma = MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"), 
        p_raw=p_raw / p_raw.sum(),
        p_cal=p_cal,
        p_market=p_market,
        p_market_no=p_market_no,
        buy_no_quote_available=np.ones(nb, dtype=bool),
        alpha=1.0,
        bins=bins,
        member_maxes=member_maxes,
        calibrator=cal,
        lead_days=lead,
        unit="F",
        posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
        rng_seed=123,
    )
    held_idx = 2  # an empty low bin -> q_no ~ 0.99 (ceiling regime)
    assert 1.0 - float(ma.p_posterior[held_idx]) > 0.98, "setup must put the held bin in the ceiling regime"
    return ma, held_idx


def _q_lcb_no(ma: MarketAnalysis, bin_idx: int, n: int = 600) -> float:
    """Run the NO bootstrap and restore the LCB to probability space (q_lcb = edge_lcb + c_b).

    Mirrors event_reactor_adapter restore at :3149 (q_lcb = ci_lower + cost_no).
    """
    ci_lo, _ci_hi, _p = ma._bootstrap_bin_no(bin_idx, n)
    return ci_lo + float(ma.buy_no_market_price(bin_idx))


def _q_lcb_yes(ma: MarketAnalysis, bin_idx: int, n: int = 600) -> float:
    ci_lo, _ci_hi, _p = ma._bootstrap_bin(bin_idx, n)
    return ci_lo + float(ma.p_market[bin_idx])


def test_no_ci_lcb_does_not_exceed_point_on_high_q_no_bin():
    """ANTIBODY: q_lcb_no <= q_live_no on a high-q_no bin.

    PRE-FIX this FAILS — the historical-Platt estimator inflates the 5th-percentile q_no above the
    MAP point (BUG #129). POST-FIX it PASSES because the bootstrap is grounded in the SAME MAP Platt
    params as the point, and a construction-level clamp guarantees the restored LCB <= point.
    """
    cal = _calibrator_with_qno_inflating_history()
    ma, held = _eleven_bin_high_qno_analysis(cal)

    q_live_no = 1.0 - float(ma.p_posterior[held])
    q_lcb_no = _q_lcb_no(ma, held)

    assert q_lcb_no <= q_live_no + 1e-9, (
        f"q_lcb_no={q_lcb_no:.6f} exceeds q_live_no={q_live_no:.6f} "
        f"(delta={q_lcb_no - q_live_no:+.6f}) — CI haircut bypassed (BUG #129)"
    )


def test_no_ci_invariant_holds_for_every_bin():
    """Universal antibody: q_lcb_no <= q_posterior_no for EVERY executable NO bin."""
    cal = _calibrator_with_qno_inflating_history()
    ma, _ = _eleven_bin_high_qno_analysis(cal)

    for bin_idx in range(len(ma.bins)):
        if not ma.supports_buy_no_edges(bin_idx):
            continue
        q_lcb_no = _q_lcb_no(ma, bin_idx)
        q_point_no = 1.0 - float(ma.p_posterior[bin_idx])
        assert q_lcb_no <= q_point_no + 1e-9, (
            f"NO bin {bin_idx} ({ma.bins[bin_idx].label}): "
            f"q_lcb={q_lcb_no:.6f} > q_point={q_point_no:.6f}"
        )


def test_yes_ci_lcb_does_not_exceed_point_on_high_q_yes_bin():
    """ANTIBODY (symmetric): q_lcb_yes <= q_point_yes on a high-q_YES bin.

    The audit notes the SAME estimator-mismatch defect class exists on the YES bootstrap; live YES
    inversions are 0 only because live YES bins are low-q. This drives the YES held bin (the bin that
    holds the ensemble mass) into the high-q ceiling regime, where the historical-Platt estimator can
    lift the YES LCB above the point exactly as on the NO side. PRE-FIX this FAILS; POST-FIX the
    symmetric MAP-grounding + clamp makes q_lcb_yes <= point by construction.
    """
    cal = _calibrator_with_qno_inflating_history()
    ma, _ = _eleven_bin_high_qno_analysis(cal)
    yes_held = int(np.argmax(ma.p_posterior))  # the bin that holds the ensemble mass (high q_YES)
    q_lcb_yes = _q_lcb_yes(ma, yes_held)
    q_point_yes = float(ma.p_posterior[yes_held])
    assert q_lcb_yes <= q_point_yes + 1e-9, (
        f"YES bin {yes_held}: q_lcb={q_lcb_yes:.6f} > q_point={q_point_yes:.6f} "
        f"(delta={q_lcb_yes - q_point_yes:+.6f}) — symmetric CI haircut bypassed"
    )
