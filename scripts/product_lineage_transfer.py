#!/usr/bin/env python3
# Created: 2026-05-28
# Authority basis: operator critique 2026-05-28 — Test A mixed TIGGE+OpenData residuals;
#   live product is OpenData. Must stratify by product lineage and test transfer.
# Purpose: READ-ONLY. From the clean 12-city HIGH evidence ledger, stratify residuals by
#   product (TIGGE prior vs ECMWF OpenData live), and answer three questions per
#   (city, season) bucket:
#     A1  TIGGE-only blocked-by-date OOS: does the TIGGE prior carry a real, stable bias?
#     A2  OpenData-only leave-one-date-out OOS: does the live product carry the same bias?
#     A3  TRANSFER: does the TIGGE-derived bias improve OpenData rows (the live question)?
#   Products have disjoint dates, so A3 is naturally out-of-product. Writes CSV + summary.
import csv
import sys
from collections import defaultdict

import numpy as np

CSV = "/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical/docs/operations/sd3_validation_evidence/ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv"
OUT = "/Users/leofitz/.claude/jobs/866db2ea/product_stratified_high.csv"
RNG = np.random.default_rng(20260528)


def product(dv):
    d = dv.lower()
    return "opendata" if "opendata" in d else ("tigge" if "tigge" in d else "other")


def load():
    rows = []
    for r in csv.DictReader(open(CSV)):
        rows.append({"city": r["city"], "season": r["season"], "td": r["target_date"],
                     "prod": product(r["data_version"]), "resid": float(r["residual_c"])})
    return rows


def rmse(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else float("nan")


def mae(x):
    return float(np.mean(np.abs(x))) if len(x) else float("nan")


def kfold_oos(ev, k):
    """blocked-by-date OOS corrected residuals (subtract fold-trained mean)."""
    dates = sorted(set(e["td"] for e in ev))
    fold = {d: i % k for i, d in enumerate(dates)}
    raw, corr = [], []
    for tf in range(k):
        tr = [e["resid"] for e in ev if fold[e["td"]] != tf]
        te = [e for e in ev if fold[e["td"]] == tf]
        if not tr or not te:
            continue
        b = float(np.mean(tr))
        for e in te:
            raw.append(e["resid"]); corr.append(e["resid"] - b)
    return raw, corr


def loo_by_date(ev):
    """leave-one-date-out: bias from all other dates' rows."""
    by_date = defaultdict(list)
    for e in ev:
        by_date[e["td"]].append(e)
    raw, corr = [], []
    allres = [e["resid"] for e in ev]
    n = len(allres); s = sum(allres)
    for d, rows in by_date.items():
        others = [e["resid"] for e in ev if e["td"] != d]
        if not others:
            continue
        b = float(np.mean(others))
        for e in rows:
            raw.append(e["resid"]); corr.append(e["resid"] - b)
    return raw, corr


def main():
    rows = load()
    # global date-range sanity per product
    for p in ("tigge", "opendata"):
        ds = sorted(r["td"] for r in rows if r["prod"] == p)
        if ds:
            print(f"{p}: n={len(ds)} dates {ds[0]}..{ds[-1]}")

    by = defaultdict(lambda: {"tigge": [], "opendata": []})
    for r in rows:
        if r["prod"] in ("tigge", "opendata"):
            by[(r["city"], r["season"])][r["prod"]].append(r)

    hdr = ["city", "season", "n_tigge", "n_opd", "bias_tigge", "bias_opd",
           "opd_raw_mae", "opd_corr_TIGGEbias_mae", "opd_corr_OPDloo_mae",
           "transfer_improve(raw-corrTIGGE)", "A1_tigge_oos_rmse_raw", "A1_tigge_oos_rmse_corr",
           "transfer_verdict"]
    out = []
    transfer_improvements = []
    for (city, season), d in sorted(by.items()):
        tg, op = d["tigge"], d["opendata"]
        n_t, n_o = len(tg), len(op)
        bias_t = float(np.mean([e["resid"] for e in tg])) if tg else float("nan")
        bias_o = float(np.mean([e["resid"] for e in op])) if op else float("nan")
        # A1: TIGGE-only blocked-by-date OOS
        a1_raw = a1_corr = float("nan")
        if n_t >= 9:
            k = 5 if len(set(e["td"] for e in tg)) >= 20 else 3
            r_, c_ = kfold_oos(tg, k)
            if r_:
                a1_raw, a1_corr = rmse(r_), rmse(c_)
        # transfer: apply TIGGE-all bias to OPD rows (disjoint dates => OOS)
        if n_o >= 1 and tg:
            opd_res = np.array([e["resid"] for e in op])
            opd_raw_mae = mae(opd_res)
            opd_corr_tigge_mae = mae(opd_res - bias_t)
            improve = opd_raw_mae - opd_corr_tigge_mae   # >0 => TIGGE bias helps OPD
            # OPD-only LOO
            opd_corr_loo_mae = float("nan")
            if n_o >= 3:
                lr, lc = loo_by_date(op)
                if lr:
                    opd_corr_loo_mae = mae(lc)
            verdict = "TIGGE_HELPS_OPD" if improve > 0.05 else ("NEUTRAL" if abs(improve) <= 0.05 else "TIGGE_HURTS_OPD")
            transfer_improvements.append(improve)
            out.append([city, season, n_t, n_o, round(bias_t, 2), round(bias_o, 2),
                        round(opd_raw_mae, 2), round(opd_corr_tigge_mae, 2),
                        round(opd_corr_loo_mae, 2) if opd_corr_loo_mae == opd_corr_loo_mae else "n/a",
                        round(improve, 2),
                        round(a1_raw, 2) if a1_raw == a1_raw else "n/a",
                        round(a1_corr, 2) if a1_corr == a1_corr else "n/a",
                        verdict])
        else:
            out.append([city, season, n_t, n_o, round(bias_t, 2),
                        round(bias_o, 2) if bias_o == bias_o else "n/a",
                        "n/a", "n/a", "n/a", "n/a",
                        round(a1_raw, 2) if a1_raw == a1_raw else "n/a",
                        round(a1_corr, 2) if a1_corr == a1_corr else "n/a",
                        "NO_OPD_ROWS"])

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(out)

    print(f"\n{'city':14s}{'seas':5s}{'nT':>4s}{'nO':>4s}{'biasT':>7s}{'biasO':>7s}{'OPDraw':>7s}{'+TIGGE':>7s}{'OPDloo':>7s}{'impr':>6s}  verdict")
    print("-" * 104)
    for r in out:
        print(f"{r[0]:14s}{r[1]:5s}{r[2]:>4}{r[3]:>4}{str(r[4]):>7}{str(r[5]):>7}{str(r[6]):>7}{str(r[7]):>7}{str(r[8]):>7}{str(r[9]):>6}  {r[12]}")

    ti = [x for x in transfer_improvements]
    if ti:
        wins = sum(1 for x in ti if x > 0.05); losses = sum(1 for x in ti if x < -0.05)
        neutral = len(ti) - wins - losses
        print(f"\nTRANSFER (TIGGE bias -> OpenData rows): buckets={len(ti)} wins={wins} losses={losses} neutral={neutral}")
        print(f"  mean improvement={np.mean(ti):+.2f} C   median={np.median(ti):+.2f} C   (>0 = TIGGE bias helps live OPD)")
    print(f"csv: {OUT}")


if __name__ == "__main__":
    main()
