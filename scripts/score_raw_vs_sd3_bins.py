#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-29
# Authority basis: operator redesign 2026-05-28 — sd3 proper-score test, 12-city scope.
# Purpose: READ-ONLY. Bin-level proper-score comparison raw(none) vs sd3(full_transport_v1),
#   HIGH metric, 12 cities. UNPAIRED per-city aggregate (the §4.1 method) — one distribution
#   per decision_group_id; mean Brier/LogLoss/RPS per family per city. Reuses production
#   proper-score primitives from scripts/audit_refit_proper_scores.py.
#   NOTE: sd3 here = CONTAMINATED stored ft_v1 (12z window + settlement-unit bug baked in at
#   fit time). This corroborates that the SHIPPED sd3 model is bad; the CLEAN candidate is
#   tested separately at temperature level (phase2_oos_bias.py).
# Lifecycle: created=2026-05-28; last_reviewed=2026-05-29; last_reused=never
# Reuse: Inspect hardcoded DB/ROOT/CITIES constants; confirm audit_refit_proper_scores.py path is accessible before running.
from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from collections import defaultdict

import numpy as np

ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical"
spec_a = importlib.util.spec_from_file_location("aud", f"{ROOT}/scripts/audit_refit_proper_scores.py")
aud = importlib.util.module_from_spec(spec_a); sys.modules["aud"] = aud; spec_a.loader.exec_module(aud)

DB = "/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-forecasts.db"
CITIES = ["Jeddah", "Shanghai", "Busan", "Jakarta", "San Francisco", "NYC",
          "Seoul", "Hong Kong", "Istanbul", "Paris", "Austin", "London"]
METRIC = "high"
OUT_CSV = "/Users/leofitz/.claude/jobs/866db2ea/score_12city_high.csv"


def load_dists(conn, family):
    q = """
      SELECT city, range_label, p_raw, outcome, decision_group_id
      FROM calibration_pairs_v2
      WHERE temperature_metric=? AND bin_source='canonical_v2'
        AND error_model_family=? AND city IN (%s)
        AND p_raw IS NOT NULL AND decision_group_id IS NOT NULL AND decision_group_id!=''
    """ % ",".join("?" * len(CITIES))
    rows = conn.execute(q, [METRIC, family, *CITIES]).fetchall()
    by_gid = defaultdict(list)
    for r in rows:
        by_gid[r["decision_group_id"]].append(r)
    dists = []
    skipped = 0
    for gid, bins in by_gid.items():
        p_sum = sum(b["p_raw"] for b in bins)
        n_out = sum(b["outcome"] for b in bins)
        if abs(p_sum - 1.0) > 1e-3 or n_out != 1 or len(bins) < 80:
            skipped += 1
            continue
        sb = sorted(bins, key=lambda b: aud._parse_bin_lower(b["range_label"])[0])
        p = np.array([b["p_raw"] for b in sb], dtype=float)
        yidx = next(i for i, b in enumerate(sb) if b["outcome"] == 1)
        dists.append({"city": sb[0]["city"], "p": p, "y": yidx})
    return dists, skipped


def agg(dists):
    if not dists:
        return None
    ll = np.mean([aud._logloss_dist(d["p"], d["y"]) for d in dists])
    rps = np.mean([aud._rps_dist(d["p"], d["y"]) for d in dists])
    br = np.mean([aud._brier_dist(d["p"], d["y"]) for d in dists])
    pa = np.mean([aud._p_actual(d["p"], d["y"]) for d in dists])
    return {"n": len(dists), "ll": float(ll), "rps": float(rps), "br": float(br), "pa": float(pa)}


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1;")
    raw, sk_r = load_dists(conn, "none")
    sd3, sk_s = load_dists(conn, "full_transport_v1")
    conn.close()
    print(f"raw dists={len(raw)} (skipped {sk_r})  sd3 dists={len(sd3)} (skipped {sk_s})")

    raw_by_city = defaultdict(list); sd3_by_city = defaultdict(list)
    for d in raw:
        raw_by_city[d["city"]].append(d)
    for d in sd3:
        sd3_by_city[d["city"]].append(d)

    hdr = ["city", "n_raw", "n_sd3", "LL_raw", "LL_sd3", "RPS_raw", "RPS_sd3",
           "Brier_raw", "Brier_sd3", "Pactual_raw", "Pactual_sd3", "sd3_wins_of_3"]
    out = []
    print(f"\n{'city':15s}{'nR':>6s}{'nS':>6s}{'LL_raw':>8s}{'LL_sd3':>8s}{'RPS_raw':>8s}{'RPS_sd3':>8s}{'Br_raw':>8s}{'Br_sd3':>8s}{'win/3':>6s}")
    print("-" * 95)
    for city in CITIES + ["__GLOBAL__"]:
        if city == "__GLOBAL__":
            r, s = agg(raw), agg(sd3)
        else:
            r, s = agg(raw_by_city.get(city, [])), agg(sd3_by_city.get(city, []))
        if not r or not s:
            out.append([city] + ["n/a"] * 11); print(f"{city:15s}  (insufficient)"); continue
        wins = sum([s["ll"] < r["ll"], s["rps"] < r["rps"], s["br"] < r["br"]])
        out.append([city, r["n"], s["n"], round(r["ll"],4), round(s["ll"],4),
                    round(r["rps"],4), round(s["rps"],4), round(r["br"],4), round(s["br"],4),
                    round(r["pa"],4), round(s["pa"],4), wins])
        print(f"{city:15s}{r['n']:>6d}{s['n']:>6d}{r['ll']:>8.4f}{s['ll']:>8.4f}{r['rps']:>8.4f}{s['rps']:>8.4f}{r['br']:>8.4f}{s['br']:>8.4f}{wins:>6d}")
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(out)
    print(f"\ncsv: {OUT_CSV}")
    print("NOTE: sd3 = CONTAMINATED stored ft_v1 (12z+unit bug). Lower LL/RPS/Brier = better. win/3 = metrics where sd3 beats raw.")


if __name__ == "__main__":
    main()
