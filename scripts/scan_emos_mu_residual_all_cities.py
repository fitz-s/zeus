# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: D4 emos_mu_bias_probe.md + law 8. Task step 2: scan ALL cities for a material
#   cold μ*−settlement residual (mean < −0.5°C over adequate n); correct ONLY those. READ-ONLY.
# Method: reconstruct live μ* = a + b·x̄_ensemble (per-city members_unit → °C) from ensemble_snapshots
#   + state/emos_calibration.json; join VERIFIED settlement_outcomes; report per (city,season,high) the
#   signed residual + an OOS walk-forward gate (does a residual-grounded μ-offset reduce |residual| AND
#   improve Gaussian CRPS, embargoed, no leak). Prints the CORRECT vs LEAVE-ALONE verdict per cell.
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sqlite3
from collections import defaultdict

import numpy as np
from scipy.stats import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("ZEUS_STATE_DIR", os.path.join(REPO, "state"))
DB = os.path.join(STATE, "zeus-forecasts.db")
EMOS = os.path.join(STATE, "emos_calibration.json")

COLD_THRESHOLD = -0.5     # task: material cold residual mean < −0.5°C
MIN_N = 8                 # adequate settled n for a verdict
MIN_TRAIN = 8             # walk-forward min training residuals to fit the offset


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
    if sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    return sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi))


def main():
    emos = json.load(open(EMOS))["cells"]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
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

    cur.execute(
        "SELECT city,target_date,temperature_metric,lead_hours,members_json,members_unit "
        "FROM ensemble_snapshots WHERE temperature_metric='high' AND contributes_to_target_extrema=1"
    )
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
        if (str(mu_unit or "")).strip().lower() in ("degf", "f"):
            members = (members - 32.0) / 1.8
        xbar = float(np.mean(members))
        s2 = max(float(np.var(members, ddof=1)), 1e-6)
        td = _dt.date.fromisoformat(d)
        seas = season(td.month)
        cell = emos.get(f"{c}|{seas}|high")
        if not cell or cell.get("served") != "emos":
            continue
        a, b, cc, dd, ee = (float(x) for x in cell["params"])
        mu = a + b * xbar
        sig = math.sqrt(math.exp(cc + dd * math.log(s2) + ee * (lh / 24.0)))
        rows.append({"city": c, "season": seas, "date": td, "mu": mu, "sig": sig,
                     "settled": settle[(c, d, m)]})

    by_cs = defaultdict(list)
    for r in rows:
        by_cs[(r["city"], r["season"])].append(r)

    cold_cells = []
    gate_pass = []
    print(f"{'cell':24s} {'n':>3s} {'mean_res':>9s} {'med_res':>8s}  verdict")
    for (city, seas), cr in sorted(by_cs.items()):
        cr.sort(key=lambda r: r["date"])
        res = np.array([r["mu"] - r["settled"] for r in cr])
        n = len(cr)
        mean_res = float(res.mean())
        is_cold = (mean_res < COLD_THRESHOLD) and (n >= MIN_N)
        verdict = ""
        if is_cold:
            cold_cells.append((city, seas, n, mean_res))
            # OOS walk-forward gate: residual-grounded offset (median past residual, embargo 1d).
            b0_res, c_res, b0_crps, c_crps = [], [], [], []
            for i, r in enumerate(cr):
                train = [p for p in cr[:i] if (r["date"] - p["date"]).days >= 1]
                b0_res.append(r["mu"] - r["settled"])
                b0_crps.append(crps_gaussian(r["mu"], r["sig"], r["settled"]))
                if len(train) >= MIN_TRAIN:
                    delta = float(np.median([p["mu"] - p["settled"] for p in train]))
                    muC = r["mu"] - delta
                    c_res.append(muC - r["settled"])
                    c_crps.append(crps_gaussian(muC, r["sig"], r["settled"]))
            if c_res:
                # compare on the OOS subset where C is defined
                oos_idx = len(b0_res) - len(c_res)
                b0_res_oos = np.array(b0_res[oos_idx:])
                b0_crps_oos = float(np.mean(b0_crps[oos_idx:]))
                improves_res = abs(np.mean(c_res)) < abs(np.mean(b0_res_oos)) - 0.10
                improves_crps = np.mean(c_crps) < b0_crps_oos - 0.01
                if improves_res and improves_crps:
                    gate_pass.append((city, seas, len(c_res), float(np.mean(b0_res_oos)),
                                      float(np.mean(c_res)), b0_crps_oos, float(np.mean(c_crps))))
                    verdict = (f"COLD→CORRECT (OOS n={len(c_res)} res {np.mean(b0_res_oos):+.2f}→"
                               f"{np.mean(c_res):+.2f} crps {b0_crps_oos:.2f}→{np.mean(c_crps):.2f})")
                else:
                    verdict = (f"COLD but GATE-FAIL (OOS n={len(c_res)} res {np.mean(b0_res_oos):+.2f}→"
                               f"{np.mean(c_res):+.2f} crps {b0_crps_oos:.2f}→{np.mean(c_crps):.2f})")
            else:
                verdict = "COLD but THIN (no OOS window ≥ MIN_TRAIN) → fail-closed"
        elif mean_res < COLD_THRESHOLD:
            verdict = f"cold-lean but n={n} < {MIN_N} → insufficient"
        print(f"{city+'|'+seas:24s} {n:3d} {mean_res:+9.3f} {np.median(res):+8.3f}  {verdict}")

    print(f"\n=== COLD cells (mean<−0.5, n≥{MIN_N}): {len(cold_cells)} ===")
    for c in cold_cells:
        print(f"  {c[0]}|{c[1]} n={c[2]} mean={c[3]:+.3f}")
    print(f"\n=== GATE-PASS cells (correct these): {len(gate_pass)} ===")
    for g in gate_pass:
        print(f"  {g[0]}|{g[1]} oos_n={g[2]} res {g[3]:+.3f}→{g[4]:+.3f} crps {g[5]:.3f}→{g[6]:.3f}")


if __name__ == "__main__":
    main()
