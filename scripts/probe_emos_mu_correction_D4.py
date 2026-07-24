# Created: 2026-06-14
# Last reused or audited: 2026-07-24
# Authority basis: D4 emos_mu_bias_probe.md + law 8 (airport-settlement-honest center).
# Purpose: DISCRIMINATING PROBE — does the live EMOS μ* cold residual for cold-biased cities come
#   from (a) the absent S2 representativeness de-bias on x̄ BEFORE the EMOS formula, or (b) the EMOS
#   intercept already fit cold? Reconstruct μ* = a + b·x̄_ensemble from ensemble_snapshots + live
#   emos_calibration.json, compute μ*−settlement residuals vs VERIFIED settlement, then compare OOS
#   (walk-forward) the residual + CRPS for: B0 no-correction, A S2-debias-on-x̄, C residual-grounded
#   μ-offset (the intercept-recal-equivalent measured on μ*−settlement). READ-ONLY.
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sqlite3
from collections import defaultdict

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# state/ is gitignored and lives only in the live (main) tree; allow an override for worktree probes.
STATE = os.environ.get("ZEUS_STATE_DIR", os.path.join(REPO, "state"))
DB = os.path.join(STATE, "zeus-forecasts.db")
EMOS = os.path.join(STATE, "emos_calibration.json")
GRID = os.path.join(STATE, "grid_representativeness_offset.json")

TARGETS = ["Tokyo", "San Francisco", "Beijing", "Karachi"]


def season(mm):
    mm = int(mm)
    return "DJF" if mm in (12, 1, 2) else "MAM" if mm in (3, 4, 5) else "JJA" if mm in (6, 7, 8) else "SON"


def to_c(v, u):
    if v is None:
        return None
    u = (u or "").upper()
    if u in ("F", "DEGF"):
        return (v - 32.0) * 5.0 / 9.0
    if u == "K":
        return v - 273.15
    return v


def crps_gaussian(mu, sigma, y):
    # closed-form CRPS for a Gaussian forecast N(mu,sigma) and observation y (Gneiting & Raftery)
    if sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    from scipy.stats import norm
    return sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi))


def main():
    emos = json.load(open(EMOS))["cells"]
    grid = json.load(open(GRID))["cities"]

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    # settlement map: (city,date,metric)->(settled_c)
    cur.execute(
        "SELECT city,target_date,temperature_metric,settlement_value,settlement_unit "
        "FROM settlement_outcomes WHERE authority='VERIFIED' AND settlement_value IS NOT NULL "
        "AND temperature_metric='high'"
    )
    settle = {}
    for c, d, m, v, u in cur.fetchall():
        sc = to_c(float(v), u)
        if sc is not None:
            settle[(c, str(d)[:10], m)] = sc

    # ensemble snapshots: shortest qualifying genuine-forecast-lead snapshot per (city,date)
    cur.execute(
        "SELECT city,target_date,temperature_metric,lead_hours,members_json,members_unit "
        "FROM ensemble_snapshots WHERE temperature_metric='high' AND contributes_to_target_extrema=1 "
        "AND city IN ('Tokyo','San Francisco','Beijing','Karachi')"
    )
    # keep shortest lead in [24,144]h per (city,date)
    best = {}
    for c, d, m, lh, mj, mu_unit in cur.fetchall():
        try:
            lh = float(lh)
        except (TypeError, ValueError):
            continue
        if not (24.0 <= lh <= 144.0):
            continue
        key = (c, str(d)[:10], m)
        if key not in best or lh < best[key][0]:
            best[key] = (lh, mj, mu_unit)
    con.close()

    # Build per-record rows: city, season, target_date, xbar_c, settled_c, lead_days
    rows = []
    for (c, d, m), (lh, mj, mu_unit) in best.items():
        if (c, d, m) not in settle:
            continue
        try:
            members = np.asarray(json.loads(mj), dtype=float)
        except Exception:
            continue
        if members.size < 2:
            continue
        # UNIT DISCIPLINE: members_unit is per-city (Tokyo/Beijing degC, SF degF). EMOS params are
        # fit in °C, so convert members to °C BEFORE computing xbar/S2 — mirrors build_emos_q's
        # F→C member conversion. settled_c is already °C (via to_c on settlement_unit).
        mu_u = (str(mu_unit or "")).strip().lower()
        if mu_u in ("degf", "f"):
            members = (members - 32.0) / 1.8
        xbar = float(np.mean(members))
        s2 = float(np.var(members, ddof=1))
        td = _dt.date.fromisoformat(d)
        seas = season(td.month)
        cell = emos.get(f"{c}|{seas}|high")
        if not cell or cell.get("served") != "emos":
            continue
        a, b, cc, dd, ee = (float(x) for x in cell["params"])
        rows.append({
            "city": c, "season": seas, "date": td, "xbar": xbar, "s2": max(s2, 1e-6),
            "settled": settle[(c, d, m)], "lead_days": lh / 24.0,
            "a": a, "b": b, "c": cc, "d": dd, "e": ee,
        })

    rows.sort(key=lambda r: r["date"])

    def mu_b0(r):
        return r["a"] + r["b"] * r["xbar"]

    def mu_a(r):  # S2 debias on xbar BEFORE emos: corrected xbar = xbar - offset (offset<0 => warm)
        g = grid.get(r["city"])
        off = float(g["offset_c"]) if g and g.get("activated") else 0.0
        xbar_corr = r["xbar"] - off  # offset = mean(ens - obs); corrected = xbar - offset warms a cold grid
        return r["a"] + r["b"] * xbar_corr

    def sigma_c(r):
        return math.sqrt(math.exp(r["c"] + r["d"] * math.log(r["s2"]) + r["e"] * r["lead_days"]))

    print("=== μ* reconstruction validation (B0, no correction) — must match probe doc ===")
    for c in TARGETS:
        cr = [r for r in rows if r["city"] == c]
        if not cr:
            continue
        res = np.array([mu_b0(r) - r["settled"] for r in cr])
        print(f"  {c:16s} n={len(cr):3d}  mean(μ*−settled)={res.mean():+.3f}  median={np.median(res):+.3f}")
        for seas in ("MAM", "JJA"):
            crs = [r for r in cr if r["season"] == seas]
            if crs:
                rs = np.array([mu_b0(r) - r["settled"] for r in crs])
                print(f"        {seas}: n={len(crs):2d} mean={rs.mean():+.3f} median={np.median(rs):+.3f}")

    # ============ DISCRIMINATING PROBE: walk-forward OOS per city-season ============
    # B0: no correction.  A: S2-debias-on-xbar.  C: residual-grounded μ-offset (walk-forward median
    # of past μ*−settled residuals, EMBARGO 1 day, applied as mu_corr = mu - delta).
    print("\n=== WALK-FORWARD OOS per city-season (embargoed, no leakage) ===")
    print(f"{'cell':22s} {'n':>3s} {'B0_res':>8s} {'A_res':>8s} {'C_res':>8s} | "
          f"{'B0_crps':>8s} {'A_crps':>8s} {'C_crps':>8s}")
    MIN_TRAIN = 8  # need at least this many past residuals to fit C
    by_cs = defaultdict(list)
    for r in rows:
        by_cs[(r["city"], r["season"])].append(r)

    summary = {}
    for (city, seas), cr in sorted(by_cs.items()):
        cr.sort(key=lambda r: r["date"])
        b0_res, a_res, c_res = [], [], []
        b0_crps, a_crps, c_crps = [], [], []
        for i, r in enumerate(cr):
            # walk-forward training set: strictly-prior records, embargo 1 day
            train = [p for p in cr[:i] if (r["date"] - p["date"]).days >= 1]
            mu0 = mu_b0(r)
            muA = mu_a(r)
            s = sigma_c(r)
            y = r["settled"]
            b0_res.append(mu0 - y)
            a_res.append(muA - y)
            b0_crps.append(crps_gaussian(mu0, s, y))
            a_crps.append(crps_gaussian(muA, s, y))
            # C: residual-grounded offset = median of past (mu0 - settled). fail-closed if thin.
            if len(train) >= MIN_TRAIN:
                delta = float(np.median([mu_b0(p) - p["settled"] for p in train]))
                muC = mu0 - delta
                c_res.append(muC - y)
                c_crps.append(crps_gaussian(muC, s, y))
        def m(x):
            return f"{np.mean(x):+.3f}" if x else "   n/a"
        print(f"{city+'|'+seas:22s} {len(cr):3d} {m(b0_res):>8s} {m(a_res):>8s} {m(c_res):>8s} | "
              f"{(np.mean(b0_crps) if b0_crps else float('nan')):8.3f} "
              f"{(np.mean(a_crps) if a_crps else float('nan')):8.3f} "
              f"{(np.mean(c_crps) if c_crps else float('nan')):8.3f}")
        summary[(city, seas)] = {
            "n": len(cr), "n_oos_C": len(c_res),
            "b0_res": float(np.mean(b0_res)) if b0_res else None,
            "a_res": float(np.mean(a_res)) if a_res else None,
            "c_res": float(np.mean(c_res)) if c_res else None,
            "b0_crps": float(np.mean(b0_crps)) if b0_crps else None,
            "a_crps": float(np.mean(a_crps)) if a_crps else None,
            "c_crps": float(np.mean(c_crps)) if c_crps else None,
        }

    # Save machine-readable summary
    out = os.environ.get(
        "PROBE_OUT",
        os.path.join(
            REPO,
            "docs",
            "evidence",
            "deadloop_2026-06-14",
            "emos_mu_correction_probe_out.json.md",
        ),
    )
    with open(out, "w") as f:
        json.dump({str(k): v for k, v in summary.items()}, f, indent=2)
    print(f"\n[probe] wrote {out}")


if __name__ == "__main__":
    main()
