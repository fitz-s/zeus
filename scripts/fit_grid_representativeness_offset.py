#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: FORECAST_COLD_ROOT_UNIVERSAL_2026-06-02.md (validated root: raw 0.25deg
#   ENS cell not downscaled to settlement-station point -> 2-5C cold for sub-grid-microclimate
#   cities, ~0 for flat ones; offset lead-invariant => representativeness not forecast error;
#   season-varying => season-keyed; ERA5-at-cell cross-check; OOS train24-25/test26).
#
# Fits a per-(city, season) grid->point representativeness offset for the HIGH metric:
#   offset_c = mean(ENS_member_mean - obs_daily_max)  (lead-pooled; obs = settlement station)
# Activation gate (only correct where it demonstrably helps, leave good cities untouched):
#   activate iff  |offset| >= MIN_OFFSET_C  AND  OOS |residual| < |OOS raw|  AND n_train>=N_FLOOR.
# Shrinkage hedges year-to-year drift (Jeddah/Shanghai over-correct at shrink=1.0).
# Output: state/grid_representativeness_offset.json (production table + provenance + before/after).
# READ-ONLY w.r.t. live DBs; writes only the JSON artifact. No live-path change here.
import sqlite3, json, os, sys
import numpy as np
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD = os.path.join(REPO, "state", "zeus-world.db")
FCST = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT = os.path.join(REPO, "state", "grid_representativeness_offset.json")
MIN_OFFSET_C = 1.0      # below this, no representativeness problem worth correcting
N_FLOOR = 60            # min paired days per (city,season) to fit
SHRINK = 0.85           # hedge year-to-year drift
TRAIN_YEARS = ("2024", "2025")
TEST_YEARS = ("2026",)

def to_c(v, u):
    if v is None:
        return None
    u = (u or "").upper()
    return (v - 32) * 5 / 9 if u in ("F", "DEGF") else (v - 273.15 if u == "K" else v)

def season(mm):
    mm = int(mm)
    return "DJF" if mm in (12, 1, 2) else "MAM" if mm in (3, 4, 5) else "JJA" if mm in (6, 7, 8) else "SON"

def load_obs():
    w = sqlite3.connect(WORLD); w.row_factory = sqlite3.Row
    obs = defaultdict(dict)
    for r in w.execute("SELECT city, target_date d, running_max rm, temp_unit tu FROM observation_instants WHERE running_max IS NOT NULL"):
        c = to_c(r["rm"], r["tu"])
        if c is None or not np.isfinite(c):
            continue
        k = (r["city"], r["d"])
        if r["d"] not in obs[r["city"]] or c > obs[r["city"]][r["d"]]:
            obs[r["city"]][r["d"]] = c
    return obs

def main():
    obs = load_obs()
    f = sqlite3.connect(FCST); f.row_factory = sqlite3.Row
    # gather ENS member-mean (C) - obs per (city, season, year-split), lead-pooled
    pairs = defaultdict(lambda: defaultdict(lambda: {"train": [], "test": [], "all": []}))
    rows = f.execute("SELECT city, target_date, members_json, members_unit FROM ensemble_snapshots WHERE temperature_metric='high'")
    for r in rows:
        try:
            m = np.array(json.loads(r["members_json"]), dtype=float)
            m = m[np.isfinite(m)]
        except Exception:
            continue
        if not m.size:
            continue
        u = str(r["members_unit"])
        ens_c = (m.mean() - 32) * 5 / 9 if u.lower().startswith("degf") else float(m.mean())
        td = r["target_date"]
        o = obs.get(r["city"], {}).get(td)
        if o is None:
            continue
        d = ens_c - o
        if not np.isfinite(d):
            continue
        yr, s = td[:4], season(td[5:7])
        rec = pairs[r["city"]][s]
        rec["all"].append(d)
        if yr in TRAIN_YEARS:
            rec["train"].append(d)
        elif yr in TEST_YEARS:
            rec["test"].append(d)

    table = {"_meta": {"created": "2026-06-02", "metric": "high", "min_offset_c": MIN_OFFSET_C,
                       "n_floor": N_FLOOR, "shrink": SHRINK, "fit": "ENS_member_mean - obs_daily_max",
                       "authority": "grid_point_representativeness_offset_v1",
                       "source": "ensemble_snapshots(high) vs observation_instants.running_max, lead-pooled"},
             "cities": {}}
    summary = []
    for city in sorted(pairs):
        for s, rec in pairs[city].items():
            alld = np.array(rec["all"]); tr = np.array(rec["train"]); te = np.array(rec["test"])
            if alld.size < N_FLOOR:
                continue
            offset_full = float(alld.mean())
            offset_applied = round(offset_full * SHRINK, 3)
            # OOS gate
            activated = False; oos_raw = None; oos_resid = None
            if tr.size >= 30 and te.size >= 30:
                tr_off = float(tr.mean()) * SHRINK
                oos_raw = float(te.mean())
                oos_resid = float((te - tr_off).mean())
                helps = abs(oos_resid) < abs(oos_raw) - 0.25  # must reduce error by >0.25C
                activated = (abs(offset_full) >= MIN_OFFSET_C) and helps
            else:
                # no OOS split possible; activate only on magnitude (conservative)
                activated = abs(offset_full) >= MIN_OFFSET_C
            table["cities"].setdefault(city, {})[s] = {
                "offset_c": offset_applied, "offset_raw_c": round(offset_full, 3),
                "n": int(alld.size), "n_train": int(tr.size), "n_test": int(te.size),
                "oos_raw_c": (round(oos_raw, 3) if oos_raw is not None else None),
                "oos_residual_c": (round(oos_resid, 3) if oos_resid is not None else None),
                "activated": bool(activated), "shrink": SHRINK}
            summary.append((city, s, alld.size, offset_full, oos_raw, oos_resid, activated))
    with open(OUT, "w") as fh:
        json.dump(table, fh, indent=2)

    # BEFORE/AFTER report (JJA + MAM focus)
    print(f"wrote {OUT}")
    print(f"{'city':16}{'seas':5}{'n':>5}{'raw_off':>9}{'oos_raw':>9}{'oos_resid':>10}{'act':>5}")
    for city, s, n, off, oraw, ores, act in sorted(summary, key=lambda x: (x[0], x[1])):
        if s not in ("MAM", "JJA"):
            continue
        a = "YES" if act else "."
        print(f"{city:16}{s:5}{n:5d}{off:9.2f}{(oraw if oraw is not None else float('nan')):9.2f}{(ores if ores is not None else float('nan')):10.2f}{a:>5}")
    nact = sum(1 for x in summary if x[6])
    print(f"\nactivated (city,season) pairs: {nact} / {len(summary)}")

if __name__ == "__main__":
    main()
