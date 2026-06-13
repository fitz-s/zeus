#!/usr/bin/env python3
# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: workflow A4 calibration diagnosis 2026-06-13 + docs/authority statistical_calibration_addendum.
#   The LICENSE for the GATE-2 sigma-shape refit (scripts/fit_sigma_shape_kernel.py): the refit is only
#   real if it improves AFTER-COST SETTLEMENT win-rate OUT-OF-SAMPLE. In-sample is known-inflated (a
#   prior "71.7%" collapsed on temporal holdout, per project memory). This harness produces the
#   temporal-holdout ring-ratio + win-rate evidence and the known-ring-loss replay. READ-ONLY.
"""Temporal-holdout + ring-loss-replay evidence for the sigma-shape kernel refit (GATE-2).

WHAT THIS PROVES (the deliverable, not the fit alone)
  1. TEMPORAL holdout: fit (k, w, m) on settled cells with target_date < SPLIT, evaluate the dist-1 /
     dist-2 ring ratio AND an after-cost NO win-rate proxy on cells with target_date >= SPLIT. The LIVE
     uniform form (its own (k_live, w_live) refit on the SAME train split) and the CANDIDATE kernel form
     are scored on the SAME held-out cells, so the comparison is apples-to-apples and leak-free.
  2. RING-LOSS REPLAY: for every held-out settled cell, simulate the q_lcb>price NO gate under BOTH
     forms and count: (a) NO admitted that LOST (sold NO on a bin at/near the winner) — the GATE-2 loss;
     (b) how many of the live form's NO-losses the candidate prevents (flips to NO-TRADE or to a NO on a
     bin the winner did NOT land in). Reports the named ring losses (HK/KL/Denver/Karachi) explicitly
     when present in the held-out window.
  3. FAR-NO HARVEST PRESERVATION: the far-NO favorite-longshot class (NO on bins >=3 steps from the mode
     / open-shoulder catch-alls, where the winner almost never lands) must KEEP its positive edge under
     the candidate — show the far-tail coverage and the far-NO win-rate are not destroyed.

AFTER-COST NO WIN-RATE PROXY (honest, market-free)
  We do NOT have joinable executable market prices for the held-out window (market_price_history is
  stale; executable_market_snapshots is empty for these dates — same limitation the live before/after
  script documented). So the win-rate is computed on the MODEL's own admit decision against the SETTLED
  outcome, which is exactly the quantity the GATE-2 complaint is about ("the orders that filled lost
  because we sold NO on the bin that settled YES"):
    - A NO on bin b is ADMITTED when q_no_lcb(b) = 1 - q_ucb(b) > price_no(b) + cost, where price_no is
      a CONSERVATIVE market proxy = 1 - realized_freq(dist(b)) (the calibrated market the complaint says
      priced the winner correctly) and q_ucb is the model q at the upper confidence bound. Using the
      realized-frequency curve as the market is the HARSHEST honest proxy: it gives the model no edge
      unless its q genuinely deviates from the settled base rate. (A real forward test needs live fills;
      this is the strongest evidence available pre-fill, and it is the SAME decision the gate makes.)
    - A NO WINS if the bin b did NOT settle (b != winning bin); LOSES if b == winning bin.
  win_rate = wins / admits. The GATE-2 disease is LOW win-rate on near-ring NO admits (we admit NO on a
  ring bin and the winner lands there). The cost term is the after-cost haircut (default 2c, ~ Polymarket
  taker fee + half-spread); --cost overrides.

  q_lcb / q_ucb: we widen the per-cell sigma by an additional confidence factor to form the lcb/ucb the
  gate uses; here we use the calibration-curve dispersion directly via a simple normal-approx CI on q at
  each bin (q +/- z*sqrt(q(1-q)/n_eff)). This is a PROXY for the production q_lcb bootstrap; it is
  monotone in q so the RELATIVE comparison (candidate vs live) is faithful even if the absolute admit
  count differs from production. The headline metric is the DELTA (candidate - live), which is robust to
  the proxy's absolute level.

READ-ONLY over state/zeus-forecasts.db.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
import scripts.fit_sigma_scale as _live  # noqa: E402
import scripts.fit_sigma_shape_kernel as _kern  # noqa: E402

FCST_DEFAULT = os.path.join("/Users/leofitz/zeus", "state", "zeus-forecasts.db")

# Named ring losses the operator called out (the GATE-2 complaint). Matched by city substring; the
# replay reports each explicitly when its settled cell is in the held-out window.
NAMED_RING_LOSSES = ["Hong Kong", "Kuala Lumpur", "Denver", "Karachi", "HK", "KL"]


# --------------------------------------------------------------------------------------------------
# q vectors for the two forms (both reuse the live cell machinery).
# --------------------------------------------------------------------------------------------------
def _q_uniform(cell, k: float, w: float):
    """LIVE form: (1-w)*Normal(sigma*k) + w*uniform(1/n_bins)."""
    sigma = cell["sigma_impl"] * k
    base = _live._masses_from_edges(cell["edges_lo"], cell["edges_hi"], sigma)
    if w <= 0.0:
        return base
    u = 1.0 / cell["n_bins"]
    mixed = (1.0 - w) * base + w * u
    return mixed / mixed.sum()


def _q_kernel(cell, k: float, w: float, m: float, floor_steps: float = 0.0):
    """CANDIDATE form: sigma_core=max(sigma*k, floor*step); (1-w)*Normal(sigma_core)+w*Normal(sigma_core*m)."""
    return _kern._cell_q_kernel(cell, k, w, m, floor_steps)


def _split_cells(cells, split_date: str):
    train = [c for c in cells if str(c["target_date"]) < split_date]
    test = [c for c in cells if str(c["target_date"]) >= split_date]
    return train, test


def _ratio_table(cells, qfn):
    """Per-distance mean_q vs realized freq using qfn(cell)->q vector. Returns {dist:(mean_q,realized,ratio,n)}."""
    agg = defaultdict(lambda: {"sq": 0.0, "w": 0, "n": 0})
    for cell in cells:
        q = qfn(cell)
        mi = cell["mode_index"]; won = cell["won_index"]
        for i, qi in enumerate(q):
            di = cell["items"][i][2]; dm = cell["items"][mi][2]; op = cell["items"][i][3]
            if di is None or dm is None or op:
                d = "tail"
            else:
                dd = int(round(abs(di - dm) / cell["step"]))
                d = str(dd) if dd <= 3 else ">=4"
            a = agg[d]; a["sq"] += qi; a["w"] += 1 if i == won else 0; a["n"] += 1
    out = {}
    for d, a in agg.items():
        mq = a["sq"] / a["n"] if a["n"] else 0.0
        rf = a["w"] / a["n"] if a["n"] else 0.0
        out[d] = (mq, rf, (rf / mq if mq > 0 else None), a["n"])
    return out


def _dist_of(cell, i):
    di = cell["items"][i][2]; dm = cell["items"][cell["mode_index"]][2]; op = cell["items"][i][3]
    if di is None or dm is None or op:
        return None  # tail / open-shoulder
    return int(round(abs(di - dm) / cell["step"]))


def _no_gate_replay(cells, qfn, realized_by_dist, cost: float, z: float = 1.0):
    """Simulate the q_lcb_no > price_no NO admit gate on each (cell, bin) and score against settlement.

    price_no(bin) = 1 - realized_freq(dist(bin))  (the calibrated 'sharp market' the complaint cites;
        for the tail/open bins use the tail realized freq). This is the harshest honest market proxy.
    q_no(bin) = 1 - q(bin); q_no_lcb = q_no - z*sqrt(q(1-q)/n_eff_bin) (normal-approx lower bound on q_no
        -> upper bound on q used; monotone proxy for the production bootstrap lcb).
    ADMIT NO on bin b iff q_no_lcb(b) > price_no(b) + cost.
    A NO admit WINS iff b is NOT the winning bin; LOSES iff b == winning bin (sold NO on the winner).

    Returns dict with admit/win/loss counts split by ring (dist<=2) vs far (dist>=3 or tail), plus the
    list of (city,target_date,dist) for every NEAR-ring NO LOSS (the GATE-2 disease instances).
    """
    res = {
        "near_admits": 0, "near_wins": 0, "near_losses": 0,
        "far_admits": 0, "far_wins": 0, "far_losses": 0,
        "near_loss_instances": [], "near_admit_instances": [],
    }
    for cell in cells:
        q = qfn(cell)
        won = cell["won_index"]
        for i, qi in enumerate(q):
            d = _dist_of(cell, i)
            band = "tail" if d is None else (str(d) if d <= 3 else ">=4")
            rf = realized_by_dist.get(band, (0.0, 0.0, None, 0))[1]
            price_no = 1.0 - rf
            q_no = 1.0 - qi
            # normal-approx lcb on q_no using an effective per-bin n (the band count is the support).
            n_eff = max(realized_by_dist.get(band, (0, 0, None, 1))[3], 1)
            se = math.sqrt(max(qi * (1.0 - qi), 1e-9) / n_eff)
            q_no_lcb = q_no - z * se
            admit = q_no_lcb > price_no + cost
            if not admit:
                continue
            is_near = (d is not None and d <= 2)
            lost = (i == won)
            if is_near:
                res["near_admits"] += 1
                res["near_admit_instances"].append((cell["city"], cell["target_date"], d))
                if lost:
                    res["near_losses"] += 1
                    res["near_loss_instances"].append((cell["city"], cell["target_date"], d))
                else:
                    res["near_wins"] += 1
            else:
                res["far_admits"] += 1
                if lost:
                    res["far_losses"] += 1
                else:
                    res["far_wins"] += 1
    return res


def _fmt_ratio_row(d, t):
    mq, rf, r, n = t
    return f"  dist={d:<4} mean_q={mq:.4f} realized={rf:.4f} ratio={r if r is None else round(r,3)} n={n}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Temporal-holdout + ring-loss replay for the sigma-shape kernel refit.")
    ap.add_argument("--fcst", default=FCST_DEFAULT)
    ap.add_argument("--split", default="2026-06-11", help="temporal split: train < split, test >= split.")
    ap.add_argument("--unit", default="C", help="settlement unit family to evaluate (C or F).")
    ap.add_argument("--cost", type=float, default=0.02, help="after-cost haircut on the NO admit gate.")
    ap.add_argument("--min-cells", type=int, default=20, help="min TRAIN cells to fit (holdout relaxes the live 60).")
    ap.add_argument("--out", default="", help="optional path to write a markdown evidence table.")
    args = ap.parse_args()

    cby, window, _rows = _kern._load_cells(args.fcst)
    cells = cby.get(args.unit.upper(), [])
    train, test = _split_cells(cells, args.split)
    lines = []

    def emit(s=""):
        print(s); lines.append(s)

    emit("# Sigma-shape kernel refit — TEMPORAL HOLDOUT + ring-loss replay")
    emit(f"unit={args.unit.upper()}  window={window}  split={args.split}  cost={args.cost}")
    emit(f"train_cells={len(train)}  test_cells={len(test)}")
    emit("")
    if len(train) < args.min_cells or len(test) < 5:
        emit(f"INSUFFICIENT for holdout (train {len(train)} < {args.min_cells} or test {len(test)} < 5).")
        if args.out:
            open(args.out, "w").write("\n".join(lines) + "\n")
        return 0

    # Fit BOTH forms on the TRAIN split only (leak-free). Live uniform via the live fitter; candidate
    # kernel (regime-aware floor) via the composite-objective fitter.
    lk, lw, lnll = _live._fit_mle(train)                 # live uniform (k, w)
    kk, kw, km, kfl, kobj = _kern._fit_mle(train)        # candidate kernel (k, w, m, floor_steps)
    emit(f"TRAIN fit — LIVE uniform : k={lk:.4f} w={lw:.4f}")
    emit(f"TRAIN fit — CAND kernel  : k={kk:.4f} w={kw:.4f} m={km:.4f} floor_steps={kfl:.4f}")
    emit("")

    # Evaluate ring ratios on the HELD-OUT test cells under each TRAIN-fitted form.
    rt_live = _ratio_table(test, lambda c: _q_uniform(c, lk, lw))
    rt_cand = _ratio_table(test, lambda c: _q_kernel(c, kk, kw, km, kfl))
    # Realized-by-dist on the test set is the SAME for both (it's the outcome); take from either table.
    realized_by_dist = {d: rt_cand[d] for d in rt_cand}

    emit("## Held-out ring ratios (realized / expected) — target 1.0")
    emit("LIVE uniform form:")
    for d in ["0", "1", "2", "3", ">=4", "tail"]:
        if d in rt_live:
            emit(_fmt_ratio_row(d, rt_live[d]))
    emit("CANDIDATE kernel form:")
    for d in ["0", "1", "2", "3", ">=4", "tail"]:
        if d in rt_cand:
            emit(_fmt_ratio_row(d, rt_cand[d]))
    emit("")
    for d in ("1", "2"):
        rl = rt_live.get(d, (0, 0, None, 0))[2]
        rc = rt_cand.get(d, (0, 0, None, 0))[2]
        emit(f"HELD-OUT dist-{d} ratio: LIVE={rl} -> CANDIDATE={rc}  (target 1.0)")
    emit("")

    # NO-gate replay on the held-out cells under each form, scored against settlement.
    rep_live = _no_gate_replay(test, lambda c: _q_uniform(c, lk, lw), realized_by_dist, args.cost)
    rep_cand = _no_gate_replay(test, lambda c: _q_kernel(c, kk, kw, km, kfl), realized_by_dist, args.cost)

    def wr(rep, near=True):
        a = rep["near_admits"] if near else rep["far_admits"]
        wn = rep["near_wins"] if near else rep["far_wins"]
        return (wn / a if a else None), a

    nwl, nal = wr(rep_live, True); nwc, nac = wr(rep_cand, True)
    fwl, fal = wr(rep_live, False); fwc, fac = wr(rep_cand, False)
    emit("## NO-gate replay (held-out, scored vs settlement)")
    emit(f"NEAR-ring NO admits (dist<=2)  LIVE: {nal} admits, win_rate={None if nwl is None else round(nwl,3)}, "
         f"losses={rep_live['near_losses']}")
    emit(f"NEAR-ring NO admits (dist<=2)  CAND: {nac} admits, win_rate={None if nwc is None else round(nwc,3)}, "
         f"losses={rep_cand['near_losses']}")
    emit(f"FAR NO admits (dist>=3/tail)   LIVE: {fal} admits, win_rate={None if fwl is None else round(fwl,3)}")
    emit(f"FAR NO admits (dist>=3/tail)   CAND: {fac} admits, win_rate={None if fwc is None else round(fwc,3)}")
    emit("")

    # GATE-2 prevention: near-ring NO LOSSES under live that the candidate does NOT also admit-and-lose.
    live_losses = set(rep_live["near_loss_instances"])
    cand_losses = set(rep_cand["near_loss_instances"])
    prevented = live_losses - cand_losses
    emit("## GATE-2 prevention (near-ring NO losses)")
    emit(f"LIVE near-ring NO losses: {len(live_losses)}")
    emit(f"CAND near-ring NO losses: {len(cand_losses)}")
    emit(f"PREVENTED by candidate  : {len(prevented)}")
    for (city, td, d) in sorted(prevented):
        emit(f"   prevented: {city} {td} dist-{d}")
    emit("")
    emit("## Named ring losses present in held-out window")
    present = False
    for cell in test:
        if any(nm.lower() in str(cell["city"]).lower() for nm in NAMED_RING_LOSSES):
            present = True
            # was the winner near the mode? (the GATE-2 shape)
            wd = _dist_of(cell, cell["won_index"])
            in_live = any(li[0] == cell["city"] and li[1] == cell["target_date"] for li in live_losses)
            in_cand = any(li[0] == cell["city"] and li[1] == cell["target_date"] for li in cand_losses)
            emit(f"   {cell['city']} {cell['target_date']} winner_dist={wd} "
                 f"live_NO_loss={in_live} cand_NO_loss={in_cand}")
    if not present:
        emit("   (none of the named cities settled in the held-out window for this unit)")

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        open(args.out, "w").write("\n".join(lines) + "\n")
        print(f"\n--- written {args.out} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
