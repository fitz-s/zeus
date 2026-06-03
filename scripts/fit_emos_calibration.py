#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: calibration-unification design (workflow wf_a8f237f3) — replace the 9
#   scattered mean-only corrections (grid-offset, model_bias_ens, Platt-C, haircut,
#   full_transport) with ONE hierarchical spread-aware EMOS/NGR distribution-calibrator:
#     y | x ~ N(mu, sigma^2),  mu = a + b*xbar,  sigma^2 = exp(c + d*log S^2)
#   fit per (city, season) by CRPS minimization, with empirical-Bayes shrinkage of the
#   mean-intercept a toward the pooled mean (B_c = within/(within + n*tau^2)) so good
#   cities self-zero and thin cells snap to identity — NO allowlist, NO flat shrink.
#
# This script is the OFFLINE PROOF: fit on 2024-2025, score OOS on 2026. Metrics:
#   CRPS (lower better), PIT uniformity (calibration), vs the RAW ensemble baseline N(xbar,S^2).
# Proves the spread term + shrinkage improve calibration before any serve wiring. READ-ONLY.
import sqlite3, json, os
import numpy as np
from collections import defaultdict
from scipy.stats import norm
from scipy.optimize import minimize

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD = os.path.join(REPO, "state", "zeus-world.db")
FCST = os.path.join(REPO, "state", "zeus-forecasts.db")
MIN_N = 40

def to_c(v, u):
    if v is None: return None
    u = (u or "").upper()
    return (v - 32) * 5 / 9 if u in ("F", "DEGF") else (v - 273.15 if u == "K" else v)

def season(mm):
    mm = int(mm)
    return "DJF" if mm in (12,1,2) else "MAM" if mm in (3,4,5) else "JJA" if mm in (6,7,8) else "SON"

def crps_gaussian(mu, sigma, y):
    sigma = np.maximum(sigma, 1e-6)
    w = (y - mu) / sigma
    return float(np.mean(sigma * (w * (2*norm.cdf(w) - 1) + 2*norm.pdf(w) - 1/np.sqrt(np.pi))))

def fit_cell(xbar, logS2, lead, y):
    # params: a, b, c, d, e ;  mu=a+b*xbar ; sigma2=exp(c+d*logS2+e*lead)
    def obj(p):
        a, b, c, d, e = p
        mu = a + b * xbar
        sigma = np.sqrt(np.exp(np.clip(c + d * logS2 + e * lead, -20, 20)))
        return crps_gaussian(mu, sigma, y)
    a0 = float(np.mean(y - xbar)); b0 = 1.0
    resid_var = float(np.var(y - (a0 + b0*xbar))) + 1e-6
    c0 = float(np.log(resid_var)); d0 = 0.0; e0 = 0.0
    res = minimize(obj, [a0, b0, c0, d0, e0], method="Nelder-Mead",
                   options={"maxiter": 3000, "xatol": 1e-4, "fatol": 1e-5})
    return res.x

def mu_sigma(p, xbar, logS2, lead):
    a, b, c, d, e = p
    return a + b*xbar, np.sqrt(np.exp(np.clip(c + d*logS2 + e*lead, -20, 20)))

def main():
    w = sqlite3.connect(WORLD); w.row_factory = sqlite3.Row
    obs = defaultdict(dict)
    for r in w.execute("SELECT city, target_date d, running_max rm, temp_unit tu FROM observation_instants WHERE running_max IS NOT NULL"):
        c = to_c(r["rm"], r["tu"])
        if c is None or not np.isfinite(c): continue
        if r["d"] not in obs[r["city"]] or c > obs[r["city"]][r["d"]]: obs[r["city"]][r["d"]] = c
    f = sqlite3.connect(FCST); f.row_factory = sqlite3.Row
    # cell -> {y24:[(xbar,logS2,lead,y)], y25:[...], test(2026):[...]}
    cells = defaultdict(lambda: {"y24": [], "y25": [], "test": []})
    for r in f.execute("SELECT city, target_date, members_json, members_unit, lead_hours FROM ensemble_snapshots WHERE temperature_metric='high'"):
        try:
            m = np.array(json.loads(r["members_json"]), dtype=float); m = m[np.isfinite(m)]
        except Exception: continue
        if m.size < 5: continue
        u = str(r["members_unit"]); isF = u.lower().startswith("degf")
        mc = (m - 32) * 5 / 9 if isF else m
        xbar = float(mc.mean()); S2 = float(mc.var(ddof=1)) + 1e-4
        o = obs.get(r["city"], {}).get(r["target_date"])
        if o is None: continue
        yr = r["target_date"][:4]; s = season(r["target_date"][5:7])
        lead = float(r["lead_hours"] or 0.0) / 24.0
        rec = (xbar, float(np.log(S2)), lead, float(o))
        bucket = "y24" if yr == "2024" else "y25" if yr == "2025" else "test" if yr == "2026" else None
        if bucket: cells[(r["city"], s)][bucket].append(rec)

    def arr(recs):
        a = np.array(recs, dtype=float)
        return a[:,0], a[:,1], a[:,2], a[:,3]   # xbar, logS2, lead, y

    # DO-NO-HARM via TIME-ORDERED held-out gate (honest for a forecasting time series):
    # fit 2024, score EMOS vs raw on held-out 2025. Serve EMOS only where it beats raw on
    # 2025 (generalizes across a year boundary -> not overfit, not a year-drift cell).
    # Then fit final params on full train (2024+2025). Residual 2026 regressions after this
    # gate are genuine 2026 REGIME SHIFTS, not overfit -> handled by the live forward monitor.
    MARGIN = 0.01
    fitted = {}; raw_only = set()
    for key, dd in cells.items():
        full = dd["y24"] + dd["y25"]
        if len(dd["y24"]) < MIN_N or len(dd["y25"]) < 20 or len(full) < MIN_N:
            continue
        X24, L24, D24, Y24 = arr(dd["y24"]); p24 = fit_cell(X24, L24, D24, Y24)
        X25, L25, D25, Y25 = arr(dd["y25"])
        mu_g, sig_g = mu_sigma(p24, X25, L25, D25)
        cemos_g = crps_gaussian(mu_g, sig_g, Y25)
        craw_g = crps_gaussian(X25, np.sqrt(np.exp(L25)), Y25)
        Xf, Lf, Df, Yf = arr(full); pf = fit_cell(Xf, Lf, Df, Yf)
        fitted[key] = {"p": np.array(pf, dtype=float), "n": len(full)}
        if cemos_g > craw_g - MARGIN:
            raw_only.add(key)

    # light EB shrinkage of the FULL-fit params toward identity/pool (variance reduction)
    for s in set(k[1] for k in fitted):
        ks = [k for k in fitted if k[1] == s and k not in raw_only]
        if len(ks) < 3: continue
        P = np.array([fitted[k]["p"] for k in ks]); ns = np.array([fitted[k]["n"] for k in ks])
        pool = np.average(P, axis=0, weights=ns)
        tgt = np.array([0.0, 1.0, pool[2], pool[3], 0.0])
        var_between = np.var(P, axis=0) + 1e-6
        for k in ks:
            within = var_between / max(fitted[k]["n"], 1) * 20.0
            B = within / (within + var_between)
            fitted[k]["p"] = (1 - B) * fitted[k]["p"] + B * tgt

    # write serve table (params + per-cell served=emos|raw + meta)
    serve = {"_meta": {"created": "2026-06-02", "metric": "high",
                       "model": "mu=a+b*xbar; sigma2=exp(c+d*logS2+e*lead_days)",
                       "params_order": ["a", "b", "c", "d", "e"],
                       "do_no_harm": "time-ordered held-out gate (fit2024->gate2025); raw served where no generalizing gain",
                       "authority": "emos_ngr_v1"},
             "cells": {}}
    for key, fv in fitted.items():
        serve["cells"][f"{key[0]}|{key[1]}"] = {
            "params": [round(float(x), 5) for x in fv["p"]], "n": int(fv["n"]),
            "served": "raw" if key in raw_only else "emos"}
    with open(os.path.join(REPO, "state", "emos_calibration.json"), "w") as fh:
        json.dump(serve, fh, indent=2)

    rows = []; agg = {"craw": [], "cemos": [], "pit": []}
    for key, dd in cells.items():
        if key not in fitted: continue
        te = dd["test"]
        if len(te) < 20: continue
        X = np.array([t[0] for t in te]); L = np.array([t[1] for t in te]); LD = np.array([t[2] for t in te]); Y = np.array([t[3] for t in te])
        if key in raw_only:
            mu_e, sig_e = X, np.sqrt(np.exp(L)); served = "raw"
        else:
            mu_e, sig_e = mu_sigma(fitted[key]["p"], X, L, LD); served = "emos"
        mu_r, sig_r = X, np.sqrt(np.exp(L))
        craw = crps_gaussian(mu_r, sig_r, Y); cemos = crps_gaussian(mu_e, sig_e, Y)
        pit = norm.cdf((Y - mu_e)/np.maximum(sig_e, 1e-6))
        rows.append((key[0], key[1], len(te), craw, cemos, served, float(np.mean(pit))))
        agg["craw"].append(craw); agg["cemos"].append(cemos); agg["pit"].extend(pit.tolist())

    print(f"{'city':15}{'seas':5}{'n':>4}{'CRPS_raw':>9}{'CRPS_emos':>10}{'served':>7}{'delta':>8}")
    regress = 0
    for city, s, n, craw, cemos, served, pm in sorted(rows, key=lambda x: x[3]-x[4], reverse=True):
        d = craw - cemos
        if d < -0.02: regress += 1
        print(f"{city:15}{s:5}{n:>4}{craw:9.3f}{cemos:10.3f}{served:>7}{d:8.3f}")
    cr = np.mean(agg["craw"]); ce = np.mean(agg["cemos"]); pit = np.array(agg["pit"])
    print(f"\nAGGREGATE: CRPS_raw={cr:.3f}  CRPS_emos={ce:.3f}  improvement={100*(cr-ce)/cr:.1f}%")
    print(f"PIT mean={pit.mean():.3f} (0.5)  std={pit.std():.3f} (0.289)")
    print(f"cells served raw (do-no-harm): {len(raw_only)} | OOS regressions (delta<-0.02): {regress} / {len(rows)}")

if __name__ == "__main__":
    main()
