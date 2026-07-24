# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12
#   ("stale forecast = decayed evaluation — quantify it"). READ-ONLY,
#   mode=ro&immutable=1, SELECT-only, writes nothing to any DB.
#   Q3: calibration quality (Brier / LogLoss) as a function of posterior staleness,
#   plus information-arrival rate (mean |Δq| per hour between consecutive posteriors).
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

DB = "file:state/zeus-forecasts.db?mode=ro&immutable=1"
BIN_RE = re.compile(r"(-?\d+)\s*°?C")          # extract integer °C from a question/bin string
BELOW_RE = re.compile(r"or below|or lower", re.I)
ABOVE_RE = re.compile(r"or above|or higher", re.I)


def parse_ts(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def bin_label_temp(s):
    """Return the integer °C embedded in a bin/question string, or None."""
    m = BIN_RE.search(s)
    return int(m.group(1)) if m else None


def pctl(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def wilson_ci(successes, n, z=1.96):
    """Wilson interval for a mean of 0/1 — used to bound bucket Brier means crudely via bootstrap-free CI on n."""
    if n == 0:
        return (None, None)
    return None  # not used for continuous metrics; CI via SE below


def mean_ci(xs):
    if len(xs) < 2:
        return (None, None)
    m = st.mean(xs)
    se = st.pstdev(xs) / math.sqrt(len(xs))
    return (m - 1.96 * se, m + 1.96 * se)


def main():
    con = sqlite3.connect(DB, uri=True)
    cur = con.cursor()

    # Settled winning bins (VERIFIED)
    settled = {}
    for city, td, metric, wbin in cur.execute(
        "SELECT city, target_date, temperature_metric, winning_bin FROM settlement_outcomes "
        "WHERE authority='VERIFIED' AND winning_bin IS NOT NULL"
    ):
        t = bin_label_temp(wbin)
        if t is not None:
            settled[(city, td, metric)] = (t, wbin)

    # All posteriors with q_json, joined to a settled family
    rows = cur.execute(
        "SELECT city, target_date, temperature_metric, source_cycle_time, computed_at, q_json "
        "FROM forecast_posteriors WHERE q_json IS NOT NULL ORDER BY city, target_date, temperature_metric, computed_at"
    ).fetchall()

    # Build per-posterior scored records
    records = []   # dict per posterior
    fam_posts = defaultdict(list)  # (city,td,metric) -> list of (computed_at, q_on_temp_grid)
    for city, td, metric, sct, comp, qjson in rows:
        key = (city, td, metric)
        comp_dt = parse_ts(comp)
        sct_dt = parse_ts(sct)
        try:
            q = json.loads(qjson)
        except Exception:
            continue
        # collapse question-string bins -> integer-°C grid prob
        grid = defaultdict(float)
        below_mass = 0.0
        above_mass = 0.0
        for label, p in q.items():
            t = bin_label_temp(label)
            if t is None:
                continue
            grid[t] += float(p)
        if not grid:
            continue
        fam_posts[key].append((comp_dt, dict(grid)))

        if key not in settled:
            continue
        wtemp, wbin = settled[key]
        # probability mass assigned to the winning integer-°C bin
        p_win = grid.get(wtemp, 0.0)
        # Brier (multiclass) = sum_k (q_k - y_k)^2 ; y=1 only at winning temp
        brier = 0.0
        for t, p in grid.items():
            y = 1.0 if t == wtemp else 0.0
            brier += (p - y) ** 2
        # any winning-temp not in grid still contributes (0-1)^2=1
        if wtemp not in grid:
            brier += 1.0
        # LogLoss on the winning bin (clip)
        pc = min(max(p_win, 1e-6), 1 - 1e-6)
        logloss = -math.log(pc)

        lead_days = None
        if comp_dt:
            # lead = target_date (local midnight proxy) - computed_at date, in days
            td_dt = parse_ts(td + "T00:00:00+00:00")
            lead_days = (td_dt - comp_dt).total_seconds() / 86400.0
        cyc_age_at_comp = None
        if comp_dt and sct_dt:
            cyc_age_at_comp = (comp_dt - sct_dt).total_seconds() / 3600.0
        records.append(dict(key=key, comp=comp_dt, sct=sct_dt, p_win=p_win,
                            brier=brier, logloss=logloss, lead_days=lead_days,
                            cyc_age=cyc_age_at_comp, wtemp=wtemp))

    print("=" * 90)
    print("Q3 DECAY — calibration (Brier / LogLoss / p(win-bin)) vs STALENESS")
    print(f"scored posterior×settlement records: {len(records)}  over "
          f"{len(set(r['key'] for r in records))} settled families")
    print("=" * 90)

    # AXIS 1: posterior AGE relative to the FRESHEST (last) posterior of the same family.
    # For each family, the last posterior = age 0; earlier ones get age = comp_last - comp.
    last_comp = {}
    for r in records:
        c = r["comp"]
        if c and (r["key"] not in last_comp or c > last_comp[r["key"]]):
            last_comp[r["key"]] = c
    for r in records:
        lc = last_comp.get(r["key"])
        r["age_vs_fresh_h"] = (lc - r["comp"]).total_seconds() / 3600.0 if (lc and r["comp"]) else None

    def bucket_age(h):
        if h is None:
            return "unk"
        if h < 3:
            return "0:<3h"
        if h < 6:
            return "1:3-6h"
        if h < 12:
            return "2:6-12h"
        if h < 24:
            return "3:12-24h"
        return "4:>24h"

    print("\n[A] By posterior age relative to the FRESHEST posterior of the SAME settled family")
    print("    (age 0 = the latest belief we held; older = staler belief at the same decision)")
    agg = defaultdict(lambda: dict(brier=[], ll=[], pw=[]))
    for r in records:
        b = bucket_age(r["age_vs_fresh_h"])
        agg[b]["brier"].append(r["brier"])
        agg[b]["ll"].append(r["logloss"])
        agg[b]["pw"].append(r["p_win"])
    print(f"    {'bucket':10s} {'n':>5s} {'Brier':>8s} {'Brier95CI':>20s} {'LogLoss':>8s} {'p(win)':>8s}")
    for b in sorted(agg):
        d = agg[b]
        lo, hi = mean_ci(d["brier"])
        ci = f"[{lo:.3f},{hi:.3f}]" if lo is not None else "n/a"
        print(f"    {b:10s} {len(d['brier']):5d} {st.mean(d['brier']):8.3f} {ci:>20s} "
              f"{st.mean(d['ll']):8.3f} {st.mean(d['pw']):8.3f}")

    # AXIS 2: age of the model cycle the posterior consumed, at compute time (cyc_age hours)
    print("\n[B] By age of the consumed model cycle at compute time (computed_at - source_cycle_time)")
    def bucket_cyc(h):
        if h is None:
            return "unk"
        if h < 12:
            return "0:<12h"
        if h < 18:
            return "1:12-18h"
        if h < 24:
            return "2:18-24h"
        if h < 36:
            return "3:24-36h"
        return "4:>36h"
    agg2 = defaultdict(lambda: dict(brier=[], ll=[], pw=[]))
    for r in records:
        b = bucket_cyc(r["cyc_age"])
        agg2[b]["brier"].append(r["brier"])
        agg2[b]["ll"].append(r["logloss"])
        agg2[b]["pw"].append(r["p_win"])
    print(f"    {'bucket':10s} {'n':>5s} {'Brier':>8s} {'LogLoss':>8s} {'p(win)':>8s}")
    for b in sorted(agg2):
        d = agg2[b]
        print(f"    {b:10s} {len(d['brier']):5d} {st.mean(d['brier']):8.3f} "
              f"{st.mean(d['ll']):8.3f} {st.mean(d['pw']):8.3f}")

    # AXIS 3: forecast lead (days from compute to target) — the dominant skill axis, for context
    print("\n[C] By forecast lead (target_date - computed_at), days — context for decay vs lead")
    def bucket_lead(d):
        if d is None:
            return "unk"
        if d < 0.5:
            return "0:<0.5d (same/next-am)"
        if d < 1.5:
            return "1:~1d"
        if d < 2.5:
            return "2:~2d"
        return "3:>=3d"
    agg3 = defaultdict(lambda: dict(brier=[], ll=[], pw=[]))
    for r in records:
        b = bucket_lead(r["lead_days"])
        agg3[b]["brier"].append(r["brier"])
        agg3[b]["ll"].append(r["logloss"])
        agg3[b]["pw"].append(r["p_win"])
    print(f"    {'bucket':24s} {'n':>5s} {'Brier':>8s} {'LogLoss':>8s} {'p(win)':>8s}")
    for b in sorted(agg3):
        d = agg3[b]
        print(f"    {b:24s} {len(d['brier']):5d} {st.mean(d['brier']):8.3f} "
              f"{st.mean(d['ll']):8.3f} {st.mean(d['pw']):8.3f}")

    # ============ INFORMATION ARRIVAL RATE: mean |Δq| per hour between consecutive posteriors ============
    print("\n" + "=" * 90)
    print("INFORMATION-ARRIVAL RATE — mean total-variation |Δq| between consecutive posteriors")
    print("  (bounds the cost of using an old posterior: how much belief moves per hour)")
    print("=" * 90)
    tv_per_hr_by_lead = defaultdict(list)
    tv_all = []
    for key, lst in fam_posts.items():
        lst = [(c, g) for c, g in lst if c]
        lst.sort(key=lambda x: x[0])
        for i in range(1, len(lst)):
            (c0, g0), (c1, g1) = lst[i - 1], lst[i]
            dh = (c1 - c0).total_seconds() / 3600.0
            if dh <= 0 or dh > 48:
                continue
            temps = set(g0) | set(g1)
            tv = 0.5 * sum(abs(g1.get(t, 0.0) - g0.get(t, 0.0)) for t in temps)  # total variation dist 0..1
            tv_per_hr = tv / dh
            tv_all.append(tv_per_hr)
            # lead bucket at the later posterior
            td_dt = parse_ts(key[1] + "T00:00:00+00:00")
            lead = (td_dt - c1).total_seconds() / 86400.0
            lb = "0:<1d" if lead < 1.5 else ("1:~2d" if lead < 2.5 else "2:>=3d")
            tv_per_hr_by_lead[lb].append(tv_per_hr)
    print(f"  consecutive posterior pairs: {len(tv_all)}")
    if tv_all:
        print(f"  |Δq|_TV per hour  ALL : mean={st.mean(tv_all):.4f}  "
              f"p50={pctl(tv_all,.5):.4f}  p90={pctl(tv_all,.9):.4f}")
    print("  by lead at the later posterior:")
    for lb in sorted(tv_per_hr_by_lead):
        xs = tv_per_hr_by_lead[lb]
        print(f"    {lb:8s} n={len(xs):4d}  mean |Δq|/h={st.mean(xs):.4f}  p50={pctl(xs,.5):.4f}  "
              f"p90={pctl(xs,.9):.4f}   => 6h drift≈{6*st.mean(xs):.3f}TV, 12h≈{12*st.mean(xs):.3f}TV")

    con.close()


if __name__ == "__main__":
    main()
