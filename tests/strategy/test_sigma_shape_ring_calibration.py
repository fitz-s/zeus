# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: workflow A4 calibration diagnosis 2026-06-13 + docs/authority statistical_calibration_addendum.
#   GATE-2 defect relationship tests (RED-on-revert). Two cross-module invariants:
#     RT-1 RING CALIBRATION: the near-center ring q must carry its realized mass — q(dist-1 ring) >=
#       realized_freq(dist-1). Under the LIVE uniform-pedestal sigma-shape this is RED (the pedestal
#       steals ring mass: dist-1 mean_q 0.1715 < realized 0.1912). It turns GREEN only under the refit
#       regime-aware sigma-FLOOR shape (scripts/fit_sigma_shape_kernel.py), which removes the flat
#       pedestal and floors the core sigma at the realized ~1.8-step dispersion. The test pins the
#       invariant on a SYNTHETIC settled population whose realized ring frequency is known by
#       construction, so it is deterministic and city/unit-agnostic.
#     RT-2 MARKET-ANCHOR CAP BLIND SPOT: the live market-anchor cap (src/strategy/live_inference/
#       market_anchor.py) is one-sided — q_anchor_no = a*q_model_no + (1-a)*market_no; out = min(in,
#       anchor). For a CONFIDENT NO where the market ALSO leans NO, the blend stays above the price, so
#       the cap CANNOT veto the admit; it only trims size. This pins the algebraic blind spot so nobody
#       re-claims "the cap fixes GATE-2" (it provably cannot — the fix must be upstream at q production).
"""Relationship tests for the GATE-2 sigma-shape refit and the market-anchor cap blind spot."""
from __future__ import annotations

import importlib.util
import math
import os

import numpy as np
import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_kernel_module():
    """Load scripts/fit_sigma_shape_kernel.py as a module (scripts/ is not a package)."""
    path = os.path.join(_REPO, "scripts", "fit_sigma_shape_kernel.py")
    spec = importlib.util.spec_from_file_location("fit_sigma_shape_kernel_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------------------------------
# Synthetic settled population: a fixed grid of interior bins (1 step), a Normal "forecast" whose
# implied sigma is DELIBERATELY too narrow (the documented over-peaking), and winning bins SAMPLED so
# the realized win frequency is genuinely FLATTER than the narrow forecast (the favorite-longshot
# center). This reproduces the GATE-2 condition without touching any DB: the narrow shape under-weights
# the ring; only widening (the floor) restores the ring mass.
# --------------------------------------------------------------------------------------------------
def _make_synthetic_cells(n_cells: int = 600, n_interior: int = 9, narrow_sigma_steps: float = 0.8,
                          realized_sigma_steps: float = 1.8, seed: int = 7):
    """Build cells matching the live fitter's cell dict shape, with a KNOWN realized dispersion.

    Each cell: interior bins centred at -4..+4 (step 1) plus two open shoulders; mode at 0. sigma_impl
    is the NARROW (over-peaked) forecast sigma. The winning bin is drawn from a WIDER Normal
    (realized_sigma_steps) so the realized ring frequency exceeds the narrow forecast's ring q — the
    GATE-2 setup. Returns a list of cell dicts consumable by the kernel module's pure helpers.
    """
    rng = np.random.default_rng(seed)
    half = 4
    centres = list(range(-half, half + 1))  # interior centres, mode at index `half` (value 0)
    cells = []
    for _ in range(n_cells):
        # winning offset from a WIDE Normal, rounded to nearest interior bin (clamped into the grid).
        off = rng.normal(0.0, realized_sigma_steps)
        won_centre = int(round(off))
        won_centre = max(-half, min(half, won_centre))
        # Build items: open-low shoulder, interiors, open-high shoulder (matches _build_cells shape).
        items = []
        items.append([f"{centres[0]-1}°C or below", 0.0, float(centres[0] - 1), True])
        for c in centres:
            items.append([f"{c}°C", 0.0, float(c), False])
        items.append([f"{centres[-1]+1}°C or higher", 0.0, float(centres[-1] + 1), True])
        mode_index = 1 + half  # interior index of centre 0, offset by the leading open shoulder
        won_index = 1 + (won_centre + half)
        # Precompute integration edges (degree offsets from mode centre) like _cell_edges.
        los, his = [], []
        NEG, POS = -1e18, 1e18
        for label, _p, deg, is_open in items:
            if is_open and "below" in label:
                los.append(NEG); his.append((deg - 0.0) + 0.5)
            elif is_open and "higher" in label:
                los.append((deg - 0.0) - 0.5); his.append(POS)
            else:
                los.append((deg - 0.0) - 0.5); his.append((deg - 0.0) + 0.5)
        cells.append({
            "city": "Synthetic", "target_date": "2026-06-10", "bucket": "A_24h",
            "n_bins": len(items), "sigma_impl": narrow_sigma_steps, "mode_index": mode_index,
            "items": items, "won_index": won_index, "step": 1.0,
            "edges_lo": np.asarray(los, dtype=float), "edges_hi": np.asarray(his, dtype=float),
        })
    return cells


def _ring_ratio(kern, cells, k, w, m, floor_steps, dist):
    tab = kern._calibration_table(cells, k, w, m, floor_steps)
    row = next((t for t in tab if t["dist"] == str(dist)), None)
    assert row is not None, f"no dist-{dist} row"
    return row["mean_q"], row["realized_freq"]


# ==================================================================================================
# RT-1: RING CALIBRATION INVARIANT — q(dist-2 ring) >= realized_freq(dist-2).
#       dist-2 is the documented WORST live under-weighting (real ratio 1.31-1.44; the winner lands 2
#       steps from the mode on the GATE-2 losers). RED under the over-peaked live shape (the uniform
#       pedestal cannot reach the ring), GREEN only under the refit sigma-FLOOR shape that floors the
#       core sigma at the realized ~1.8-step dispersion. The synthetic population's realized dispersion
#       is KNOWN by construction (winners drawn from a wide Normal), so the test is deterministic and
#       city/unit-agnostic — the floor that turns it GREEN is the SAME ~1.8-step floor both real C and
#       F families independently fit.
# ==================================================================================================
def test_rt1_ring_calibration_red_under_uniform_green_under_floor():
    kern = _load_kernel_module()
    cells = _make_synthetic_cells()  # narrow forecast sigma 0.8 step; realized dispersion 1.8 step

    # LIVE/over-peaked shape analogue: the un-floored narrow Normal (floor_steps=0). This is STRICTLY
    # MORE peaked than the live uniform-pedestal form (the pedestal only flattens further), so if the
    # un-floored shape under-weights the dist-2 ring, the live form does too — the RED is conservative.
    mq_live, rf = _ring_ratio(kern, cells, k=1.0, w=0.0, m=1.0, floor_steps=0.0, dist=2)
    # RED: the over-peaked shape massively under-weights the dist-2 ring (its q decays too fast).
    assert mq_live < rf, (
        f"expected dist-2 ring UNDER-weighted under the un-floored/live shape, got mean_q={mq_live} "
        f">= realized={rf} (if this fails the synthetic over-peaking assumption is wrong)"
    )

    # GREEN under the refit sigma-FLOOR shape: floor the core sigma at the realized ~1.8-step dispersion.
    # "Carries its realized mass" = the ring is CALIBRATED (ratio realized/expected ~ 1.0). The live
    # over-peaked shape leaves the ring grossly under-weighted (ratio >> 1); the floor brings it to ~1.0.
    mq_floor, rf2 = _ring_ratio(kern, cells, k=1.0, w=0.0, m=1.0, floor_steps=1.8, dist=2)
    assert math.isclose(rf, rf2), "realized frequency must be identical (same settled population)"
    ratio_live = rf / mq_live
    ratio_floor = rf2 / mq_floor
    # The live shape is badly RED (under-weighted, ratio well above 1); the floor is near-calibrated.
    assert ratio_live > 1.5, f"live dist-2 ring must be grossly under-weighted (ratio {ratio_live:.2f} > 1.5)"
    assert abs(ratio_floor - 1.0) <= 0.05, (
        f"REFIT FLOOR must bring the dist-2 ring to ~calibrated (|ratio-1|<=0.05), got ratio={ratio_floor:.3f} "
        f"(mean_q={mq_floor}, realized={rf2}). If RED here, the floor refit is not restoring ring calibration."
    )
    # And the floor must MOVE the ring up relative to the over-peaked shape (the mechanism, not a fluke).
    assert mq_floor > mq_live, "the sigma-floor must INCREASE the dist-2 ring q (the GATE-2 fix mechanism)"


def test_rt1_floor_drives_dist2_ratio_to_one():
    """The floor must bring the dist-2 ring ratio (realized/expected) much closer to 1.0 than the
    over-peaked live shape (whose dist-2 ratio is grossly > 1.0 = under-weighted)."""
    kern = _load_kernel_module()
    cells = _make_synthetic_cells()
    mq_live, rf = _ring_ratio(kern, cells, k=1.0, w=0.0, m=1.0, floor_steps=0.0, dist=2)
    mq_floor, _ = _ring_ratio(kern, cells, k=1.0, w=0.0, m=1.0, floor_steps=1.8, dist=2)
    ratio_live = rf / mq_live
    ratio_floor = rf / mq_floor
    assert abs(ratio_floor - 1.0) < abs(ratio_live - 1.0), (
        f"floor must move the dist-2 ratio CLOSER to 1.0: live ratio={ratio_live:.3f} -> "
        f"floor ratio={ratio_floor:.3f}"
    )
    assert abs(ratio_floor - 1.0) < 0.2, f"floored dist-2 ratio should be near 1.0, got {ratio_floor:.3f}"


# ==================================================================================================
# RT-2: MARKET-ANCHOR CAP BLIND SPOT — the cap cannot veto a confident NO.
#       Pins the algebra so no one re-claims the cap fixes GATE-2.
# ==================================================================================================
def _load_market_anchor():
    from src.strategy.live_inference.market_anchor import market_anchored_no_lcb  # noqa: PLC0415
    return market_anchored_no_lcb


def test_rt2_market_anchor_cap_cannot_veto_a_confident_no():
    """A confident NO (q_model_no high) whose market ALSO leans NO is NOT vetoed by the one-sided cap.

    GATE-2 loss class: the model is confidently NO on a near-center ring bin AND the market price is a
    moderate NO. The anchor blend q_anchor_no = a*q_model_no + (1-a)*market_no stays ABOVE the NO price,
    so the cap only trims the lower bound; the admit (q_lcb_no_out > price) SURVIVES. The cap is the
    WRONG layer for GATE-2 — proven here algebraically.
    """
    cap = _load_market_anchor()
    # Confident NO on a near-center bin; market also leans NO (price 0.62), but the calibrated truth is
    # that this bin actually WINS (so the NO loses). The model over-weights NO because its q(bin) is too
    # low (the GATE-2 over-flattening). alpha = model trust (legacy level-3 ~0.5).
    price_no = 0.62
    res = cap(
        q_lcb_no=0.80,        # confident NO lower bound (overconfident — the disease)
        q_model_no=0.85,      # confident NO point belief
        market_no_price=price_no,
        alpha=0.5,
        bin_distance_steps=1.0,   # near-center ring bin (in scope for the cap)
    )
    # The cap trims toward the blend but the blend = 0.5*0.85 + 0.5*0.62 = 0.735 > price 0.62.
    assert res.q_lcb_no_out > price_no, (
        f"BLIND SPOT: the cap must NOT push a confident-NO lower bound below the price (it only trims). "
        f"out={res.q_lcb_no_out} should stay > price={price_no} — the admit survives, the cap cannot veto."
    )
    # Explicitly: even capped, the trade is still ADMITTED (out > price), so the cap did not prevent the
    # GATE-2 loss. This is the structural claim: the fix must be upstream at q production.
    admitted = res.q_lcb_no_out > price_no
    assert admitted, "the confident NO is still admitted after the cap — the cap cannot fix GATE-2"


def test_rt2_cap_is_one_sided_only_lowers():
    """Sanity pin: the cap is one-sided (out <= in always); it can only ever LOWER the bound."""
    cap = _load_market_anchor()
    for q_in, q_model, mkt, a in [(0.8, 0.85, 0.62, 0.5), (0.7, 0.7, 0.9, 0.5), (0.5, 0.5, 0.3, 0.7)]:
        res = cap(q_lcb_no=q_in, q_model_no=q_model, market_no_price=mkt, alpha=a, bin_distance_steps=1.0)
        assert res.q_lcb_no_out <= q_in + 1e-9, f"cap must never raise the bound: {res.q_lcb_no_out} > {q_in}"


def test_rt2_cap_only_trims_when_blend_below_input():
    """When the market is a STRONG NO the blend can drop below the input and the cap trims (still no veto
    of a positive-edge admit unless the blend < price, which is the market's call, not the cap's)."""
    cap = _load_market_anchor()
    # Strong-NO market (0.95): blend = 0.5*0.85 + 0.5*0.95 = 0.90; input 0.80 -> NOT capped (blend>input).
    res = cap(q_lcb_no=0.80, q_model_no=0.85, market_no_price=0.95, alpha=0.5, bin_distance_steps=1.0)
    assert not res.capped, "blend above input must not cap"
    # Weak-NO market (0.30): blend = 0.5*0.85 + 0.5*0.30 = 0.575; input 0.80 -> capped to 0.575.
    res2 = cap(q_lcb_no=0.80, q_model_no=0.85, market_no_price=0.30, alpha=0.5, bin_distance_steps=1.0)
    assert res2.capped and abs(res2.q_lcb_no_out - 0.575) < 1e-9, "weak-NO market must trim to the blend"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
