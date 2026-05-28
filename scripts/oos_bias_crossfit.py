#!/usr/bin/env python3
# Created: 2026-05-28
# Authority basis: operator redesign 2026-05-28 — Principle 1 (raw dominates unless OOS-proven).
# Purpose: READ-ONLY. Cross-fitted (blocked-by-target_date) OOS test of the LOCATION (bias)
#   correction on the CLEAN evidence ledger. Question: does subtracting the fold-trained
#   bias_hat reduce |ensemble_mean - settlement| OUT-OF-SAMPLE vs raw (no correction)?
#   This isolates "real stable offset" from "noise" before any bin-level proper-score MC.
#   Per (city, season): K-fold by date; bias_hat = mean(train residual); score OOS errors.
import csv
import sys
from collections import defaultdict

import numpy as np

CSV = "/Users/leofitz/.claude/jobs/866db2ea/ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv"
OUT = "/Users/leofitz/.claude/jobs/866db2ea/phase2_oos_bias_high.csv"
RNG = np.random.default_rng(20260528)
MIN_FOR_5FOLD = 20
MIN_FOR_3FOLD = 9


def load():
    rows = []
    with open(CSV) as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "city": r["city"], "season": r["season"],
                    "target_date": r["target_date"],
                    "resid": float(r["residual_c"]),
                })
            except (KeyError, ValueError):
                continue
    return rows


def kfold_by_date(dates, k):
    """Assign each unique date to a fold; return dict date->fold."""
    uniq = sorted(set(dates))
    return {d: (i % k) for i, d in enumerate(uniq)}


def oos_errors(events, k):
    """Return (raw_abs[], corr_abs[], raw_sq[], corr_sq[], mean_bias_full).
    Blocked by target_date: bias_hat trained on folds != test fold."""
    fold_of = kfold_by_date([e["target_date"] for e in events], k)
    raw_abs, corr_abs, raw_sq, corr_sq = [], [], [], []
    for test_fold in range(k):
        train = [e for e in events if fold_of[e["target_date"]] != test_fold]
        test = [e for e in events if fold_of[e["target_date"]] == test_fold]
        if not train or not test:
            continue
        bias_hat = float(np.mean([e["resid"] for e in train]))
        for e in test:
            r = e["resid"]
            c = r - bias_hat            # corrected residual (OOS)
            raw_abs.append(abs(r)); corr_abs.append(abs(c))
            raw_sq.append(r * r); corr_sq.append(c * c)
    mean_bias_full = float(np.mean([e["resid"] for e in events]))
    return raw_abs, corr_abs, raw_sq, corr_sq, mean_bias_full


def boot_lcb(improv, n=3000, pct=5.0):
    if len(improv) < 3:
        return float("nan")
    a = np.array(improv, float)
    return float(np.percentile([RNG.choice(a, len(a), replace=True).mean() for _ in range(n)], pct))


def main():
    rows = load()
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[(r["city"], r["season"])].append(r)

    out = []
    hdr = ["city", "season", "n_events", "n_dates", "k", "mean_bias_c",
           "rmse_raw", "rmse_corr_oos", "mae_raw", "mae_corr_oos",
           "lcb_abs_improve", "verdict"]
    for (city, season), ev in sorted(by_bucket.items()):
        n = len(ev); ndates = len(set(e["target_date"] for e in ev))
        if ndates < MIN_FOR_3FOLD:
            out.append([city, season, n, ndates, 0, round(np.mean([e["resid"] for e in ev]), 3),
                        "n/a", "n/a", "n/a", "n/a", "n/a", "INSUFFICIENT_DATES"])
            continue
        k = 5 if ndates >= MIN_FOR_5FOLD else 3
        ra, ca, rs, cs, mb = oos_errors(ev, k)
        if not ra:
            out.append([city, season, n, ndates, k, round(mb, 3), "n/a", "n/a", "n/a", "n/a", "n/a", "NO_FOLDS"])
            continue
        rmse_raw = float(np.sqrt(np.mean(rs))); rmse_corr = float(np.sqrt(np.mean(cs)))
        mae_raw = float(np.mean(ra)); mae_corr = float(np.mean(ca))
        improve = [abs_r - abs_c for abs_r, abs_c in zip(ra, ca)]  # >0 => correction better
        lcb = boot_lcb(improve)
        # verdict: correction wins OOS only if RMSE improves AND bootstrap LCB of |error| improvement > 0
        if rmse_corr < rmse_raw and lcb > 0:
            verdict = "CORRECTION_WINS_OOS"
        elif rmse_corr < rmse_raw:
            verdict = "improves_but_LCB<=0"
        else:
            verdict = "RAW_WINS"
        out.append([city, season, n, ndates, k, round(mb, 3),
                    round(rmse_raw, 3), round(rmse_corr, 3),
                    round(mae_raw, 3), round(mae_corr, 3), round(lcb, 4), verdict])

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(out)

    print(f"{'city':15s}{'seas':5s}{'n':>4s}{'dt':>4s}{'k':>2s}{'bias':>8s}{'rmseR':>8s}{'rmseC':>8s}{'maeR':>7s}{'maeC':>7s}{'lcb':>9s}  verdict")
    print("-" * 104)
    for r in out:
        if r[6] == "n/a":
            print(f"{r[0]:15s}{r[1]:5s}{r[2]:>4d}{r[3]:>4d}{r[4]:>2}{r[5]:>8}{'':>8}{'':>8}{'':>7}{'':>7}{'':>9}  {r[11]}")
            continue
        print(f"{r[0]:15s}{r[1]:5s}{r[2]:>4d}{r[3]:>4d}{r[4]:>2d}{r[5]:>8.2f}{r[6]:>8.2f}{r[7]:>8.2f}{r[8]:>7.2f}{r[9]:>7.2f}{r[10]:>9.4f}  {r[11]}")
    # summary
    wins = sum(1 for r in out if r[11] == "CORRECTION_WINS_OOS")
    raww = sum(1 for r in out if r[11] == "RAW_WINS")
    weak = sum(1 for r in out if r[11] == "improves_but_LCB<=0")
    print(f"\nBUCKETS: CORRECTION_WINS_OOS={wins}  improves_but_LCB<=0={weak}  RAW_WINS={raww}  insufficient={sum(1 for r in out if r[11] in ('INSUFFICIENT_DATES','NO_FOLDS'))}")
    print(f"csv: {OUT}")


if __name__ == "__main__":
    main()
