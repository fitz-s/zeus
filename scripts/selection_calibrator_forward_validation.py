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
    """Fine distance class for Layer-1/decisive observations: d0/d1/d2/d3p/tail."""
    _lbl, _qy, deg, opn = items[i]
    if opn or deg is None or items[mode_i][2] is None:
        return "tail"
    d = int(round(abs(deg - items[mode_i][2]) / step))
    return f"d{d}" if d <= 2 else "d3p"


def _coarse_bin_class(i, mode_i) -> str:
    """Coarse bin_class matching the EXECUTED rows' vocabulary {modal, nonmodal}. The executed
    settlement_attribution rows do not record per-bin distance, so the corpus must use the SAME
    coarse class for the cell keys to match in the walk-forward replay/fit. modal = the argmax-q
    bin, nonmodal = everything else."""
    return "modal" if i == mode_i else "nonmodal"


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
        # NO-LEAK BOUNDARY FIX: settlement_outcomes.settled_at is the GRADING BATCH timestamp
        # (clusters on backfill dates), NOT when the outcome became known. The true known-at is the
        # day AFTER the market's target_date (the high/low for day D is known at end of D). Use
        # target_date + 1 day 00:00 UTC as the no-leak boundary so a decision at T only consumes
        # markets RESOLVED before T.
        known_at = _known_at_from_target_date(td)
        for i, (_lbl, qy, _deg, _opn) in enumerate(items):
            try:
                q = float(min(max(float(qy), 0.0), 1.0))
            except (TypeError, ValueError):
                continue
            # COARSE bin_class so the corpus cell keys match the EXECUTED rows' {modal, nonmodal}
            # vocabulary (the executed rows cannot recover per-bin distance). The fine distance class
            # is recomputed in Layer-1/decisive observations directly where needed.
            bc = _coarse_bin_class(i, mode_i)
            out.append((known_at, "YES", lead_days, bc, q, 1 if i == won_i else 0))
            out.append((known_at, "NO", lead_days, bc, 1.0 - q, 1 if i != won_i else 0))
    return out


def _known_at_from_target_date(target_date) -> str:
    """The ISO timestamp at which a market's outcome became known: target_date + 1 day 00:00 UTC.
    Used as the no-leak boundary instead of the grading-batch settled_at."""
    try:
        d = _dt.date.fromisoformat(str(target_date)[:10])
        kn = _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=1)
        return kn.isoformat()
    except Exception:
        return fsc._normalize_iso(target_date)


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
            "       traded_bin_label, category, target_date "
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


# --------------------------------------------------------------------------------------------------
# STEP-4: 3-way WALK-FORWARD replay (pure-A corpus / pure-B executed / selected-EB hybrid).
# This is THE promotion gate — no look-ahead. At each executed decision T the artifact is rebuilt
# from rows settled strictly before T.
# --------------------------------------------------------------------------------------------------

def _corpus_to_settled_rows(corpus_rows):
    """Convert the harness corpus tuples to fitter SettledDecisionRow (bin_class = distance class)."""
    out = []
    for (sa, side, lead_days, dc, raw, won) in corpus_rows:
        out.append(fsc.SettledDecisionRow(sa, side, lead_days, dc, raw, won))
    return out


def _executed_to_settled_rows(executed):
    """Convert executed settlement_attribution rows to selected SettledDecisionRow. bin_class is
    unknown for executed rows (no per-bin distance recorded) -> use 'nonmodal' (the toxic NO class)
    for NO and 'modal' for YES as the coarse proxy the cell key can resolve."""
    out = []
    for b in executed:
        side = "NO" if str(b["direction"]).lower() == "buy_no" else "YES"
        q_in_bin = float(b["q_in_bin"])
        raw = (1.0 - q_in_bin) if side == "NO" else q_in_bin
        bin_class = "nonmodal" if side == "NO" else "modal"
        # Same no-leak boundary as the corpus: target_date + 1 day (known-at), not the grading batch.
        known_at = _known_at_from_target_date(b.get("target_date")) if b.get("target_date") else fsc._normalize_iso(b["settled_at"])
        out.append(fsc.SettledDecisionRow(known_at, side, 1.0, bin_class, raw, int(b["won"] or 0)))
    return out


def threeway_walk_forward(executed, corpus_rows, min_n: int, tau) -> dict:
    """3-way as-of-decision replay. For each executed bet at time T, rebuild each population's
    artifact from rows settled < T and decide admit/block at the bet's fill cost. Report admit/block,
    toxic-NO-blocked, genuine-YES-preserved, after-cost notional-weighted EV, prequential log score.
    """
    corpus_settled = sorted(_corpus_to_settled_rows(corpus_rows), key=lambda r: r.settled_at)
    sel_settled = sorted(_executed_to_settled_rows(executed), key=lambda r: r.settled_at)

    def _bet_known_at(b):
        return _known_at_from_target_date(b.get("target_date")) if b.get("target_date") else fsc._normalize_iso(b["settled_at"])

    ex_sorted = sorted(executed, key=_bet_known_at)

    def empty_stats():
        return {"admit": 0, "block": 0, "toxic_no_blocked": 0, "toxic_no_total": 0,
                "genuine_yes_admit": 0, "genuine_yes_total": 0,
                "ev_sum": 0.0, "ev_admit_sum": 0.0, "nll": 0.0, "scored": 0}

    pops = {"pure_A_corpus": empty_stats(), "pure_B_executed": empty_stats(), "selected_EB": empty_stats()}

    for b in ex_sorted:
        T = _bet_known_at(b)
        side = "NO" if str(b["direction"]).lower() == "buy_no" else "YES"
        q_in_bin = float(b["q_in_bin"])
        raw = (1.0 - q_in_bin) if side == "NO" else q_in_bin
        fill = float(b["avg_fill_price"] or (0.70 if side == "NO" else 0.30))
        won = int(b["won"] or 0)
        bin_class = "nonmodal" if side == "NO" else "modal"
        pb = sc.raw_prob_bucket(raw)[0]
        is_toxic_no = (side == "NO" and pb >= 15)
        is_genuine_yes = (side == "YES" and 0.05 <= raw <= 0.50)
        realized_pay = (1.0 - fill) if won else (-fill)

        prior_corpus = [r for r in corpus_settled if r.settled_at < T]
        prior_selected = [r for r in sel_settled if r.settled_at < T]

        # Pure A: corpus-only v1 fit.
        art_A = fsc.fit_cells(prior_corpus, min_n=min_n, posterior_version=fsc.POSTERIOR_VERSION) if prior_corpus else None
        # Pure B: executed-only v1 fit (the selected rows as the whole population).
        art_B = fsc.fit_cells(prior_selected, min_n=min_n, posterior_version=fsc.POSTERIOR_VERSION) if prior_selected else None
        # Selected EB: corpus prior + selected likelihood.
        art_EB = (fsc.fit_eb_cells(corpus_rows=prior_corpus, selected_rows=prior_selected,
                                   min_n=min_n, posterior_version=fsc.POSTERIOR_VERSION, tau=tau)
                  if (prior_corpus and prior_selected) else None)

        for name, art in (("pure_A_corpus", art_A), ("pure_B_executed", art_B), ("selected_EB", art_EB)):
            st = pops[name]
            if is_toxic_no:
                st["toxic_no_total"] += 1
            if is_genuine_yes:
                st["genuine_yes_total"] += 1
            if art is None:
                st["block"] += 1
                if is_toxic_no:
                    st["toxic_no_blocked"] += 1
                continue
            v = sc.apply_selection_calibrator(
                raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class, artifact=art,
            )
            admit = bool(v.trade) and (v.q_safe - fill) > 0
            if admit:
                st["admit"] += 1
                st["ev_admit_sum"] += realized_pay
                if is_genuine_yes:
                    st["genuine_yes_admit"] += 1
            else:
                st["block"] += 1
                if is_toxic_no:
                    st["toxic_no_blocked"] += 1
            st["ev_sum"] += realized_pay
            # Prequential NLL using the served q_safe as the probability (clipped).
            p = min(max(v.q_safe if v.trade else (1.0 - raw if side == "NO" else raw), 1e-9), 1 - 1e-9)
            st["nll"] -= (math.log(p) if won else math.log(1.0 - p))
            st["scored"] += 1

    # Round + derive EV means.
    out = {}
    for name, st in pops.items():
        out[name] = {
            "admit": st["admit"], "block": st["block"],
            "toxic_no_blocked": st["toxic_no_blocked"], "toxic_no_total": st["toxic_no_total"],
            "genuine_yes_admit": st["genuine_yes_admit"], "genuine_yes_total": st["genuine_yes_total"],
            "after_cost_ev_admitted_sum": round(st["ev_admit_sum"], 4),
            "after_cost_ev_admitted_mean": round(st["ev_admit_sum"] / st["admit"], 4) if st["admit"] else None,
            "prequential_nll": round(st["nll"], 4),
            "prequential_nll_per_obs": round(st["nll"] / st["scored"], 4) if st["scored"] else None,
        }
    out["_note"] = ("WALK-FORWARD (as-of-decision, no look-ahead). EV is after-cost on ADMITTED bets. "
                    "The promotion bar is EV_admitted>0 FORWARD with toxic-NO blocked and genuine-YES preserved.")
    out["tau"] = tau
    out["min_n"] = min_n
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Forward-validate the selection q_lcb calibrator (walk-forward, read-only).")
    ap.add_argument("--fcst", default=FCST_DEFAULT)
    ap.add_argument("--world", default=WORLD_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--min-n", type=int, default=fsc.MIN_N_DEFAULT)
    ap.add_argument("--tau", type=float, default=None, help="EB shrinkage strength; if omitted, learned by rolling prequential log-score.")
    ap.add_argument("--no-walk-forward", action="store_true", help="use end-of-window corpus (observation) instead of as-of-decision.")
    args = ap.parse_args()

    corpus_rows = load_corpus_rows(args.fcst)
    executed = load_executed(args.world)

    # Learn tau once (rolling prequential) from the full executed-selected vs corpus if not pinned.
    sel_rows = _executed_to_settled_rows(executed)
    corpus_settled = _corpus_to_settled_rows(corpus_rows)
    tau = args.tau if args.tau is not None else fsc.learn_tau(corpus_rows=corpus_settled, selected_rows=sel_rows)

    report = {
        "_meta": {
            "authority": "selection_calibrator_forward_validation",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "fcst": args.fcst, "world": args.world,
            "posterior_version": fsc.POSTERIOR_VERSION,
            "n_corpus_side_decisions": len(corpus_rows),
            "n_executed_with_q": len(executed),
            "min_n": args.min_n,
            "tau_learned": tau,
        },
        "layer1_distribution": layer1_distribution(corpus_rows),
        "layer2_q_reliability": layer2_q_reliability(corpus_rows, args.min_n),
        "layer3_admission": layer3_admission(
            executed, corpus_rows, args.min_n, walk_forward=not args.no_walk_forward
        ),
        "step4_threeway_walk_forward": threeway_walk_forward(executed, corpus_rows, args.min_n, tau),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)

    l3 = report["layer3_admission"]
    tw = report["step4_threeway_walk_forward"]
    print(f"[forward-validation] wrote {args.out}")
    print(f"    corpus_side_decisions={len(corpus_rows)} executed_with_q={len(executed)} tau={tau}")
    print(f"    L3 (end-of-window diag): new_admit={l3['new_admit']} new_block={l3['new_block']} "
          f"of losers={l3['losers']} blocked={l3['loser_blocked']} EV old={l3['after_cost_ev_old_sum']} new={l3['after_cost_ev_new_sum']}")
    print(f"    decisive notional-weighted selection gap = {l3['decisive_test']['notional_weighted_selection_gap']}")
    print(f"    STEP-4 WALK-FORWARD 3-way (the GATE):")
    for name in ("pure_A_corpus", "pure_B_executed", "selected_EB"):
        s = tw[name]
        print(f"      {name}: admit={s['admit']} block={s['block']} "
              f"toxicNO_blocked={s['toxic_no_blocked']}/{s['toxic_no_total']} "
              f"genuineYES_admit={s['genuine_yes_admit']}/{s['genuine_yes_total']} "
              f"EV_admitted_sum={s['after_cost_ev_admitted_sum']} nll/obs={s['prequential_nll_per_obs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
