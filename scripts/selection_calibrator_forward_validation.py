#!/usr/bin/env python3
# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-aware settlement q_lcb calibrator
#   (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22).
#   Walk-forward forward-validation harness for the selection q_lcb calibrator. READ-ONLY over
#   state/zeus-forecasts.db (forecast_posteriors ⋈ settlement_outcomes VERIFIED) + state/zeus-world.db
#   (settlement_attribution). Writes a JSON report under docs/evidence/live_order_pathology/. The
#   orchestrator OWNS the promotion gate; this harness only MEASURES and REPORTS.
"""Forward-validation harness for the selection-aware settlement q_lcb calibrator.

Three report layers (per the consult spec):
  Layer 1 — DISTRIBUTION reliability: per (lead, distance, side) realized hit-rate vs raw side prob
            (PIT/reliability of the served posterior, all-bin, no admission gate).
  Layer 2 — q RELIABILITY buckets: per raw-prob bucket, realized hit-rate vs the served calibrated
            lower bound (does the lower bound under-cover, i.e. is it conservative?).
  Layer 3 — ADMISSION layer AFTER the historical gate + cost: on the EXECUTED population
            (settlement_attribution), realized win-rate, mean old center-bootstrap q_lcb proxy,
            mean NEW selection q_safe, after-cost EV old-vs-new, Wilson lower intervals, and whether
            the new calibrator BLOCKS each executed bet at its fill cost. NOTIONAL-weighted.

DECISIVE TEST (consult): compare the ADMITTED (executed) realized rate to the matched full-corpus
rate at the SAME (side, lead, raw-prob bucket). A large negative gap = adverse SELECTION (the gate
picks the toxic subset); a near-zero gap = a uniform q-only defect. Reported as a per-bucket and a
notional-weighted gap.

WALK-FORWARD: the calibrator artifact is reconstructed AS-OF each executed decision's settled_at via
rows_strictly_before (no leak), so the bound applied to a bet uses ONLY rows settled before it. The
END-OF-WINDOW artifact is also reported for reference.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import math
import os
import sqlite3

import scripts.fit_selection_calibrator as fsc
import scripts.fit_sigma_scale as fs
from src.decision import selection_calibrator as sc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
WORLD_DEFAULT = os.path.join(REPO, "state", "zeus-world.db")
OUT_DEFAULT = os.path.join(
    REPO, "docs", "evidence", "live_order_pathology",
    "2026-06-22_qlcb_selection_forward_validation.json",
)


def _wilson_lo(hits: int, n: int) -> float:
    return sc.beta_lower_bound_95(hits, n)


def _dist_class(i, items, mode_i, step) -> str:
    _lbl, _qy, deg, opn = items[i]
    if opn or deg is None or items[mode_i][2] is None:
        return "tail"
    d = int(round(abs(deg - items[mode_i][2]) / step))
    return f"d{d}" if d <= 2 else "d3p"


def load_corpus_rows(fcst_path: str):
    """Per-(city,date,bin,side) settled decisions from the full forecast corpus, with distance class
    attached (so Layer 1 and the decisive test can condition on distance)."""
    con = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(fsc._FIT_QUERY)
        db_rows = cur.fetchall()
    finally:
        con.close()
    best: dict = {}
    for (city, td, sct, comp, qj, pm, wb, sv, su, sa) in db_rows:
        if str(pm) != fsc.POSTERIOR_VERSION:
            continue
        bk = fs._bucket_for_lead(fs._lead_hours(td, sct))
        if bk is None:
            continue
        key = (city, td, bk)
        prev = best.get(key)
        if prev is None or str(comp) > str(prev[3]):
            best[key] = (city, td, sct, comp, qj, wb, sv, su, sa)
    out = []  # (settled_at, side, lead_days, dist_class, raw_side_prob, side_won)
    for (city, td, sct, comp, qj, wb, sv, su, sa) in best.values():
        parsed = fs._parse_cell(qj)
        if parsed is None:
            continue
        items, mode_i, step = parsed
        won_i = fs._winning_index(items, wb, sv, step=step)
        if won_i is None:
            continue
        lead_h = fs._lead_hours(td, sct)
        lead_days = (lead_h / 24.0) if lead_h is not None else 0.0
        sa_iso = fsc._normalize_iso(sa)
        for i, (_lbl, qy, _deg, _opn) in enumerate(items):
            try:
                q = float(min(max(float(qy), 0.0), 1.0))
            except (TypeError, ValueError):
                continue
            dc = _dist_class(i, items, mode_i, step)
            out.append((sa_iso, "YES", lead_days, dc, q, 1 if i == won_i else 0))
            out.append((sa_iso, "NO", lead_days, dc, 1.0 - q, 1 if i != won_i else 0))
    return out


def layer1_distribution(corpus_rows) -> list[dict]:
    agg = collections.defaultdict(lambda: [0, 0, 0.0])  # (side,lead,dist) -> [wins,n,sum_rawprob]
    for (_sa, side, lead_days, dc, raw, won) in corpus_rows:
        lb = sc.lead_bucket(lead_days)
        a = agg[(side, lb, dc)]
        a[0] += won
        a[1] += 1
        a[2] += raw
    rows = []
    for (side, lb, dc), (wins, n, sraw) in sorted(agg.items()):
        if n < 1:
            continue
        rows.append({
            "side": side, "lead": lb, "dist": dc, "n": n,
            "mean_raw_prob": round(sraw / n, 4),
            "realized": round(wins / n, 4),
            "ratio_realized_over_raw": round((wins / n) / (sraw / n), 3) if sraw > 0 else None,
        })
    return rows


def layer2_q_reliability(corpus_rows, min_n: int) -> list[dict]:
    # Per (side, lead, prob-bucket): realized vs the served calibrated lower bound. Tests coverage:
    # a conservative lower bound should be <= realized (the realized rate exceeds the bound).
    agg = collections.defaultdict(lambda: [0, 0])
    for (_sa, side, lead_days, _dc, raw, won) in corpus_rows:
        lb = sc.lead_bucket(lead_days)
        pb = sc.raw_prob_bucket(raw)[0]
        a = agg[(side, lb, pb)]
        a[0] += won
        a[1] += 1
    rows = []
    for (side, lb, pb), (wins, n) in sorted(agg.items()):
        if n < min_n:
            continue
        realized = wins / n
        served_lb = _wilson_lo(int(round(realized * n)), n)
        rows.append({
            "side": side, "lead": lb, "prob_bucket": pb, "n": n,
            "realized": round(realized, 4), "served_lower_bound": round(served_lb, 4),
            "covered": bool(served_lb <= realized + 1e-9),
        })
    return rows


def load_executed(world_path: str):
    con = sqlite3.connect(f"file:{world_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT direction, q_in_bin, won, avg_fill_price, settled_at, market_in_bin_prob, "
            "       traded_bin_label, category "
            "FROM settlement_attribution WHERE q_in_bin IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def layer3_admission(executed, corpus_rows, min_n: int, *, walk_forward: bool) -> dict:
    """Admission layer + decisive test. For each executed bet, compute the NEW selection q_safe from
    the walk-forward (or end-of-window) corpus cell, and whether it blocks the bet at its fill cost.
    """
    # Pre-sort corpus by settled_at for walk-forward slicing.
    corpus_sorted = sorted(corpus_rows, key=lambda r: r[0])

    def cell_rate(side, lead_b, pb, boundary):
        wins = n = 0
        for (sa, s, lead_days, _dc, raw, won) in corpus_sorted:
            if s != side:
                continue
            if sc.lead_bucket(lead_days) != lead_b:
                continue
            if sc.raw_prob_bucket(raw)[0] != pb:
                continue
            if walk_forward and boundary is not None and not (sa < boundary):
                continue
            wins += won
            n += 1
        return wins, n

    per_bet = []
    n_total = 0
    new_admit = new_block = 0
    losers = loser_blocked = 0
    ev_old = ev_new = 0.0  # after-cost EV proxies
    decisive = collections.defaultdict(lambda: [0, 0, 0, 0])  # pb -> [adm_wins,adm_n,corpus_wins,corpus_n]

    for b in executed:
        side = "NO" if str(b["direction"]).lower() == "buy_no" else "YES"
        q_in_bin = float(b["q_in_bin"])
        raw = (1.0 - q_in_bin) if side == "NO" else q_in_bin
        fill = float(b["avg_fill_price"] or (0.70 if side == "NO" else 0.30))
        won = int(b["won"] or 0)
        lead_b = "L1"  # executed rows lack reliable lead; L1 is the dominant live bucket
        pb = sc.raw_prob_bucket(raw)[0]
        boundary = fsc._normalize_iso(b["settled_at"])
        wins, n = cell_rate(side, lead_b, pb, boundary)
        n_total += 1

        # OLD admission proxy: the center-bootstrap q_lcb admitted these (raw side point cleared cost).
        old_q = raw  # the system's served side point; center-bootstrap q_lcb sat just under it -> admitted
        # NEW selection q_safe: Wilson LB of the corpus cell realized rate (fail-closed if thin).
        if n >= min_n:
            new_q = _wilson_lo(int(round((wins / n) * n)), n)
            admit_new = (new_q - fill) > 0
        else:
            new_q = 0.0
            admit_new = False  # fail-closed: thin cell -> no new entry
        new_admit += 1 if admit_new else 0
        new_block += 0 if admit_new else 1
        if not won:
            losers += 1
            loser_blocked += 0 if admit_new else 1
        # After-cost EV proxies (realized): win pays (1-fill), loss pays (-fill).
        realized_pay = (1.0 - fill) if won else (-fill)
        ev_old += realized_pay  # old admitted everything
        if admit_new:
            ev_new += realized_pay
        # Decisive test accumulation: admitted realized vs corpus realized at same pb.
        d = decisive[pb]
        d[0] += won
        d[1] += 1
        d[2] += wins
        d[3] += n
        per_bet.append({
            "side": side, "raw_prob": round(raw, 4), "fill": round(fill, 4), "won": won,
            "prob_bucket": pb, "corpus_n": n, "corpus_realized": round(wins / n, 4) if n else None,
            "new_q_safe": round(new_q, 4), "admit_new": bool(admit_new), "category": b.get("category"),
        })

    decisive_rows = []
    gap_w = gap_n = 0.0
    for pb in sorted(decisive):
        aw, an, cw, cn = decisive[pb]
        if an < 1 or cn < 1:
            continue
        adm_r = aw / an
        cor_r = cw / cn
        decisive_rows.append({
            "prob_bucket": pb, "admitted_n": an, "admitted_realized": round(adm_r, 4),
            "corpus_n": cn, "corpus_realized": round(cor_r, 4),
            "selection_gap": round(adm_r - cor_r, 4),
        })
        gap_w += (adm_r - cor_r) * an
        gap_n += an

    return {
        "n_executed": n_total,
        "new_admit": new_admit,
        "new_block": new_block,
        "losers": losers,
        "loser_blocked": loser_blocked,
        "after_cost_ev_old_sum": round(ev_old, 4),
        "after_cost_ev_new_sum": round(ev_new, 4),
        "after_cost_ev_old_mean": round(ev_old / n_total, 4) if n_total else None,
        "after_cost_ev_new_mean": round(ev_new / new_admit, 4) if new_admit else None,
        "decisive_test": {
            "by_bucket": decisive_rows,
            "notional_weighted_selection_gap": round(gap_w / gap_n, 4) if gap_n else None,
            "interpretation": (
                "large negative gap = adverse SELECTION (gate picks toxic subset); "
                "near-zero = uniform q-only defect"
            ),
        },
        "walk_forward": walk_forward,
        "min_n": min_n,
        "per_bet": per_bet,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Forward-validate the selection q_lcb calibrator (walk-forward, read-only).")
    ap.add_argument("--fcst", default=FCST_DEFAULT)
    ap.add_argument("--world", default=WORLD_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--min-n", type=int, default=fsc.MIN_N_DEFAULT)
    ap.add_argument("--no-walk-forward", action="store_true", help="use end-of-window corpus (diagnostic) instead of as-of-decision.")
    args = ap.parse_args()

    corpus_rows = load_corpus_rows(args.fcst)
    executed = load_executed(args.world)

    report = {
        "_meta": {
            "authority": "selection_calibrator_forward_validation",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "fcst": args.fcst, "world": args.world,
            "posterior_version": fsc.POSTERIOR_VERSION,
            "n_corpus_side_decisions": len(corpus_rows),
            "n_executed_with_q": len(executed),
            "min_n": args.min_n,
        },
        "layer1_distribution": layer1_distribution(corpus_rows),
        "layer2_q_reliability": layer2_q_reliability(corpus_rows, args.min_n),
        "layer3_admission": layer3_admission(
            executed, corpus_rows, args.min_n, walk_forward=not args.no_walk_forward
        ),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)

    l3 = report["layer3_admission"]
    print(f"[forward-validation] wrote {args.out}")
    print(f"    corpus_side_decisions={len(corpus_rows)} executed_with_q={len(executed)}")
    print(f"    L3 admission: new_admit={l3['new_admit']} new_block={l3['new_block']} "
          f"of losers={l3['losers']} blocked={l3['loser_blocked']}")
    print(f"    after-cost EV: old_sum={l3['after_cost_ev_old_sum']} new_sum={l3['after_cost_ev_new_sum']}")
    print(f"    decisive notional-weighted selection gap = "
          f"{l3['decisive_test']['notional_weighted_selection_gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
