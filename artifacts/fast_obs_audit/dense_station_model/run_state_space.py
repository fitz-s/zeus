"""A0 / A / B out-of-sample experiment for the settled-extreme state-space model (read-only; DBs opened mode=ro).

Per city: build local days (DST-correct: [local midnight, next local midnight) converted to UTC), split by date
(first 70 % train, last 30 % test), estimate every parameter on train days only, then score at every 10 minutes from
local 06:00 to the day end on the test days:
  A0     max(B, R(N(remaining forecast extreme + mean residual, sd)))  -- no state space, no observation conditioning
  A      state space, METAR / SPECI only                (exact interval likelihood, particle filter)
  B      A + dense likelihood factors (same parameters, same random streams)
  A_tn / B_tn, A_g12 / B_g12   Gaussian assumed-density main pass (approximation arms)
Outputs: per_city_ss/<city>.json, per_city_ss/<city>_decisions.csv.gz, ss_summary_table.md
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dense_model as dm  # noqa: E402
import run_dense_model as rdm  # noqa: E402
import state_space as ss  # noqa: E402
from ss_engine import Day, run_day  # noqa: E402

OUT = HERE / "per_city_ss"
TRAIN_FRAC = 0.7
N_PATHS = 20000
LAST_DAY = "2026-10-06"
ARMS = ("A", "B", "A_tn", "B_tn", "A_g12", "B_g12")
COVER = 0.8
SEL_DECISION_STEP = 60  # minutes between decisions in the inner walk-forward model selection


def r4(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 4)


# ------------------------------------------------------------------------------------------- data assembly

def forecast_series(city):
    d = json.loads(gzip.decompress((HERE / "raw" / "ecmwf_ifs_previous_day1.json.gz").read_bytes()))["cities"][city]
    t = pd.to_datetime(d["time"], utc=True)
    v = np.array([np.nan if x is None else float(x) for x in d["temp"]])
    return pd.Series(v, index=t)


def local_bounds(d, tz):
    """[local midnight, next local midnight) in UTC, the next midnight constructed before conversion."""
    z = ZoneInfo(tz)
    a = datetime.combine(d, datetime.min.time(), tzinfo=z)
    b = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=z)
    return pd.Timestamp(a).tz_convert("UTC"), pd.Timestamp(b).tz_convert("UTC")


def assemble(cfg):
    """Raw per-day material (no fitted parameter yet)."""
    city, tz = cfg["city"], cfg["tz"]
    dense, ident, live = rdm.load_dense(cfg)
    if cfg["step"] == 1:
        dense = dense[dense.index.minute % 5 == 0]
    step = 5 if cfg["step"] == 1 else cfg["step"]
    metar, minfo = rdm.load_metar(cfg["icao"])
    truth = rdm.load_truth(city)
    fc = forecast_series(city)
    fsec = fc.index.asi8 // 10 ** 9
    routine = set(minfo["routine_minutes"])
    first = max(dense.index.min().tz_convert(tz).date() + timedelta(days=1), metar.index.min().tz_convert(tz).date() + timedelta(days=1))
    days = []
    d = first
    while d <= date.fromisoformat(LAST_DAY):
        a, b = local_bounds(d, tz)
        D = (b - a).total_seconds() / 60
        gt = np.arange(-ss.PRE_MIN, D + ss.GRID_MIN / 2, ss.GRID_MIN)
        gts = a + pd.to_timedelta(gt, unit="min")
        gsec = gts.asi8 // 10 ** 9
        f = np.interp(gsec, fsec, fc.values, left=np.nan, right=np.nan)
        ok_f = np.isfinite(f).all() and not fc[(fc.index >= gts[0] - pd.Timedelta(hours=1)) & (fc.index <= gts[-1] + pd.Timedelta(hours=1))].isna().any()
        mrows = metar[(metar.index >= gts[0]) & (metar.index < b)]
        sched = pd.date_range(a, b, freq="min", inclusive="left")
        sched = sched[sched.minute.isin(routine)]
        got = mrows[(mrows.index >= a) & mrows.routine]
        drows = dense[(dense.index >= gts[0]) & (dense.index <= b)]
        n_dense_day = int(((drows.index >= a) & (drows.index < b)).sum())
        rec = dict(date=d.isoformat(), a=a, b=b, D=D, f=f, ok_f=bool(ok_f),
                   hour=np.asarray(gts.tz_convert(tz).hour), mt=((mrows.index - a).total_seconds() / 60).values,
                   mk=mrows.M.values.astype(int), routine=mrows.routine.values,
                   sched=((sched - a).total_seconds() / 60).values,
                   metar_cov=len(got) / max(len(sched), 1),
                   dt=((drows.index - a).total_seconds() / 60).values, dx=drows.values.astype(float),
                   dhour=np.asarray(drows.index.tz_convert(tz).hour),
                   dense_cov=n_dense_day / (D / step),
                   truth={s: truth[s].get(d.isoformat()) for s in ("high", "low")},
                   t0_0600=(pd.Timestamp(datetime.combine(d, datetime.min.time(), tzinfo=ZoneInfo(tz)) + timedelta(hours=6)).tz_convert("UTC") - a).total_seconds() / 60)
        days.append(rec)
        d += timedelta(days=1)
    usable = [r for r in days if r["ok_f"] and r["metar_cov"] >= COVER and r["dense_cov"] >= COVER]
    meta = dict(dense_identity=ident, metar=minfo, step=step, n_days_all=len(days), n_usable=len(usable),
                dropped=dict(no_forecast=sum(not r["ok_f"] for r in days), metar_cov=sum(r["metar_cov"] < COVER for r in days),
                             dense_cov=sum(r["dense_cov"] < COVER for r in days)))
    return usable, meta, live


# ------------------------------------------------------------------------------------------- estimation (train only)

def pairs(rows):
    M, x, h = [], [], []
    for r in rows:
        dmap = dict(zip(np.round(r["dt"], 3), zip(r["dx"], r["dhour"])))
        for t, k in zip(r["mt"], r["mk"]):
            if 0 <= t < r["D"] and round(t, 3) in dmap:
                v, hh = dmap[round(t, 3)]
                M.append(k)
                x.append(v)
                h.append(hh)
    return np.array(M, float), np.array(x, float), np.array(h, int)


SHRINK_W = 49  # 4 h centred running mean on the 5-min grid


def smooth(f, w=SHRINK_W):
    pad = np.r_[np.full(w // 2, f[0]), f, np.full(w // 2, f[-1])]
    return np.convolve(pad, np.ones(w) / w, mode="valid")[: f.size]


def fit_mean(rows, shrink):
    """Latent mean m_t = f_t + mu(h_t) [+ beta (f_t - S_4h f_t)] by least squares on METAR - f at METAR instants inside
    the day (training days). beta < 0 means part of the forecast's sub-4 h wiggle is noise."""
    Xs, ys = [], []
    for r in rows:
        gi = np.clip(np.rint((r["mt"] + ss.PRE_MIN) / ss.GRID_MIN).astype(int), 0, r["f"].size - 1)
        ind = (r["mt"] >= 0) & (r["mt"] < r["D"])
        gi = gi[ind]
        X = np.zeros((gi.size, 25))
        X[np.arange(gi.size), r["hour"][gi]] = 1
        X[:, 24] = (r["f"] - smooth(r["f"]))[gi]
        Xs.append(X)
        ys.append(r["mk"][ind] - r["f"][gi])
    X, y = np.vstack(Xs), np.concatenate(ys)
    X = X if shrink else X[:, :24]
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return dict(mu_h=beta[:24], beta=float(beta[24]) if shrink else 0.0, shrink=shrink, resid_sd=float(np.std(y - X @ beta)))


def mean_path(r, mean):
    return r["f"] + np.asarray(mean["mu_h"])[r["hour"]] + mean["beta"] * (r["f"] - smooth(r["f"]))


def drift_fit(rows, b_block, sigma, cad):
    """Latent correlation of the dense-vs-METAR error at settlement-instant lags (pairwise interval likelihood)."""
    lags, rhos, ns = [], [], []
    for mult in (1, 2, 3, 4):
        L = cad * mult
        l1, u1, l2, u2 = [], [], [], []
        for r in rows:
            dmap = dict(zip(np.round(r["dt"], 3), zip(r["dx"], r["dhour"])))
            obs = {}
            for t, k, rt in zip(r["mt"], r["mk"], r["routine"]):
                if rt and 0 <= t < r["D"] and round(t, 3) in dmap:
                    v, hh = dmap[round(t, 3)]
                    xb = v + b_block[hh // 3]
                    obs[round(t, 3)] = ((k - 0.5 - xb) / sigma, (k + 0.5 - xb) / sigma)
            for t, (lo, hi) in obs.items():
                o2 = obs.get(round(t + L, 3))
                if o2:
                    l1.append(lo), u1.append(hi), l2.append(o2[0]), u2.append(o2[1])
        if len(l1) > 50:
            f = dm.fit_rho(np.array(l1), np.array(u1), np.array(l2), np.array(u2))
            lags.append(L), rhos.append(f["rho"]), ns.append(len(l1))
    return lags, rhos, ns


def residual_segments(rows, b_block, mean, step):
    segs = []
    for r in rows:
        inday = (r["dt"] >= 0) & (r["dt"] < r["D"])
        t, x, hh = r["dt"][inday], r["dx"][inday], r["dhour"][inday]
        gi = np.clip(np.rint((t + ss.PRE_MIN) / ss.GRID_MIN).astype(int), 0, r["f"].size - 1)
        res = x + np.asarray(b_block)[hh // 3] - mean_path(r, mean)[gi]
        grid = np.arange(0, r["D"], step)
        seg = np.full(grid.size, np.nan)
        pos = np.rint(t / step).astype(int)
        okp = (pos >= 0) & (pos < grid.size) & np.isclose(t, pos * step)
        seg[pos[okp]] = res[okp]
        segs.append(seg)
    return segs


def make_day(r, mean, b_block, dense_ok=True):
    db = np.asarray(b_block)[r["dhour"] // 3]
    settled = {s: v[0] for s, v in r["truth"].items() if v is not None}
    era = {s: v[1] for s, v in r["truth"].items() if v is not None}
    return Day(r["D"], r["f"], None, r["hour"], r["mt"], r["mk"], r["sched"], r["dt"], r["dx"], db, settled=settled, era=era,
               label=r["date"], dense_ok=dense_ok, fmu=mean_path(r, mean))


def decisions_for(r, step=10):
    t = r["t0_0600"]
    return list(np.arange(t, r["D"], step))


def estimate(train, step, cad, lag_m, lag_d, shrink=False):
    M, x, h = pairs(train)
    g = ss.fit_dense_noise(M, x, h, mixture=False)
    mx = ss.fit_dense_noise(M, x, h, mixture=True)
    noise_fit = mx if mx["aic"] < g["aic"] - 2 else g
    b_block = noise_fit["b_block"]
    # dense error = d (OU drift) + w (white mixture). The drift share a of the CORE variance comes from the latent lag
    # correlation of the dense-vs-METAR error at settlement-instant lags; variance is conserved:
    # var(d) = a s1^2, core white = (1 - a) s1^2, outlier white = s2^2 - a s1^2.
    lags, rhos, ns = drift_fit(train, b_block, max(g["s1"], 0.02), cad)
    core2 = noise_fit["s1"] ** 2
    drift = ss.fit_error_drift(lags, rhos, core2) if lags else dict(a=0.0, tau_e=1.0, sd2=0.0)
    sd2 = drift["sd2"]
    s1w = math.sqrt(max(core2 - sd2, 0.02 ** 2))
    s2w = math.sqrt(max(noise_fit["s2"] ** 2 - sd2, s1w ** 2))
    mean = fit_mean(train, shrink)
    segs = residual_segments(train, b_block, mean, step)
    lags_one = [L for L in range(10, 190, 10) if L % step == 0]      # brief: 10..180 min
    lags_two = [L for L in range(10, 730, 10) if L % step == 0]      # the slow component needs lags to 12 h
    sub = (lambda L: sd2 * math.exp(-L / drift["tau_e"])) if sd2 > 0 else None
    one = ss.fit_ou_acf(segs, step, lags_one, n_scales=1, subtract=sub)
    two = ss.fit_ou_acf(segs, step, lags_two, n_scales=2, subtract=sub)
    noise = ss.DenseNoise(b_hour=tuple(b_block), s1=s1w, s2=s2w, pi=noise_fit["pi"], quantum=0.1, lag=lag_d)
    return dict(noise_fit_gauss=g, noise_fit_mixture=mx, noise_choice="mixture" if noise_fit is mx else "gauss",
                drift=dict(drift, lags=lags, rhos=[r4(v) for v in rhos], n=ns), mean=mean,
                mean_report=dict(mu_h=[r4(v) for v in mean["mu_h"]], beta=r4(mean["beta"]), shrink=shrink,
                                 resid_sd=r4(mean["resid_sd"])),
                ou_one=one, ou_two=two, noise=noise, b_block=b_block, lag_m=lag_m, lag_d=lag_d, n_pairs=int(M.size))


def latent_from(fit, drift):
    return ss.Latent(tau_s=fit["tau_s"], s2_s=fit["s2_s"], tau_f=fit["tau_f"], s2_f=fit["s2_f"],
                     tau_e=max(drift["tau_e"], 1.0), sd2=drift["sd2"])


# ------------------------------------------------------------------------------------------- workers

def _work(job):
    day, lat, noise, lag_m, decs, arm, seed = job
    out = run_day(day, lat, noise, lag_m, decs, arm, N=N_PATHS, seed=seed)
    rows = []
    for o in out:
        rec = dict(date=day.label, t0=float(o["t0"]), arm=arm, Bh=o["Bh"], Bl=o["Bl"], ess=o.get("ess"))
        for side in ("high", "low"):
            ks, p = o[side]
            rec[side] = (ks.tolist(), p.tolist())
        rows.append(rec)
    return rows


def run_jobs(jobs, workers):
    out = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(_work, jobs, chunksize=1):
            out.extend(rows)
    return out


def a0_rows(days, lag_m, decs_by_day, a0):
    out = []
    for d in days:
        for o in ss.a0_day(d, lag_m, decs_by_day[d.label], a0):
            rec = dict(date=d.label, t0=float(o["t0"]), arm="A0", Bh=o["Bh"], Bl=o["Bl"], ess=None)
            for side in ("high", "low"):
                ks, p = o[side]
                rec[side] = (ks.tolist(), p.tolist())
            out.append(rec)
    return out


# ------------------------------------------------------------------------------------------- scoring

def score_rows(rows, days_by_label, tz_hour):
    """Flatten to one record per (date, t0, arm, side) with settled truth."""
    out = []
    rng = np.random.default_rng(7)
    for r in rows:
        d = days_by_label[r["date"]]
        for side in ("high", "low"):
            if side not in d.settled:
                continue
            ks, p = np.array(r[side][0]), np.array(r[side][1])
            truth = d.settled[side]
            s = ss.score(ks, p, truth)
            B = r["Bh"] if side == "high" else r["Bl"]
            sem = 0.0
            if np.isfinite(B):
                sem = float(p[ks < B].sum()) if side == "high" else float(p[ks > B].sum())
            pit = s["pit_lo"] + rng.random() * (s["pit_hi"] - s["pit_lo"])
            out.append(dict(date=r["date"], t0=r["t0"], hour=tz_hour(r["date"], r["t0"]), arm=r["arm"], side=side,
                            era=d.era[side], truth=truth, ll=s["ll"], brier=s["brier"], top=s["top"], top_ok=s["top_ok"],
                            pt=s["pt"], pit=pit, sem_violation=sem, ess=r["ess"]))
    return pd.DataFrame(out)


def day_block_ci(df, col, arms=("A", "B"), n_boot=2000, seed=0):
    """Mean difference arms[1] - arms[0] of `col` with a day-blocked bootstrap 95 % interval."""
    piv = df[df.arm.isin(arms)].pivot_table(index=["date", "t0"], columns="arm", values=col)
    piv = piv.dropna()
    if piv.empty:
        return None
    diff = (piv[arms[1]] - piv[arms[0]]).groupby(level=0).agg(["sum", "count"])
    s, c = diff["sum"].values, diff["count"].values
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, s.size, (n_boot, s.size))
    boots = s[idx].sum(1) / c[idx].sum(1)
    return dict(mean=r4(s.sum() / c.sum()), lo=r4(np.quantile(boots, 0.025)), hi=r4(np.quantile(boots, 0.975)), days=int(s.size),
                decisions=int(c.sum()))


def lead_times(df, thr=0.9):
    """Per (date, side): first decision time with P(correct bin) >= thr, per arm (None if never)."""
    out = {}
    for (d, side, arm), g in df.groupby(["date", "side", "arm"]):
        g = g.sort_values("t0")
        hit = g[g.pt >= thr]
        out[(d, side, arm)] = float(hit.t0.iloc[0]) if len(hit) else None
    return out


def summarize(df, label):
    res = {"label": label}
    if df.empty:
        return res
    by = {}
    for (arm, side), g in df.groupby(["arm", "side"]):
        hc = g[g.top >= 0.95]
        by[f"{arm}|{side}"] = dict(
            n_days=int(g.date.nunique()), n_decisions=int(len(g)), logloss=r4(g.ll.mean()), brier=r4(g.brier.mean()),
            semantic_violation_mass_max=r4(g.sem_violation.max()), semantic_violation_decisions=int((g.sem_violation > 1e-12).sum()),
            hc95=dict(n=int(len(hc)), coverage=r4(len(hc) / len(g)), errors=int((~hc.top_ok).sum()), expected_errors=r4((1 - hc.top).sum()),
                      error_rate=r4((~hc.top_ok).mean()) if len(hc) else None),
            false_certainty_rate_all=r4(((g.top >= 0.95) & ~g.top_ok).mean()),
            pit=dict(mean=r4(g.pit.mean()), var=r4(g.pit.var()), tails_10pct=r4(((g.pit < 0.1) | (g.pit > 0.9)).mean()),
                     hist=np.histogram(g.pit, bins=10, range=(0, 1))[0].tolist()),
            by_hour={int(h): dict(n=int(len(x)), ll=r4(x.ll.mean()), brier=r4(x.brier.mean()), pit=r4(x.pit.mean()))
                     for h, x in g.groupby("hour")},
            reliability_top=[dict(bin=f"{lo:.2f}-{hi:.2f}", n=int(len(x)), mean_top=r4(x.top.mean()), freq=r4(x.top_ok.mean()))
                             for lo, hi in zip((0, .5, .7, .8, .9, .95, .99), (.5, .7, .8, .9, .95, .99, 1.0001))
                             for x in [g[(g.top >= lo) & (g.top < hi)]] if len(x)])
    res["by_arm_side"] = by
    pairs_ = {}
    for side in ("high", "low"):
        s = df[df.side == side]
        for a1, a2 in (("A", "B"), ("A0", "A"), ("A0", "B"), ("A_tn", "B_tn"), ("A_g12", "B_g12"), ("A", "A_tn"), ("A", "A_g12")):
            for col in ("ll", "brier"):
                ci = day_block_ci(s, col, (a1, a2))
                if ci:
                    pairs_[f"{side}|{col}|{a2}-{a1}"] = ci
    res["paired_day_block"] = pairs_
    lt = lead_times(df)
    leads = {}
    for side in ("high", "low"):
        xs, cat = [], Counter()
        for d in sorted({d for (d, s, a) in lt if s == side}):
            ta, tb = lt.get((d, side, "A")), lt.get((d, side, "B"))
            if ta is not None and tb is not None:
                xs.append(ta - tb)  # positive = B reaches P(correct) >= 0.9 earlier
            cat["both" if ta is not None and tb is not None else "only_A" if ta is not None else "only_B" if tb is not None
                else "neither"] += 1
        leads[side] = dict(dm.quantiles(xs) if xs else dict(n=0), days=dict(cat))
    res["lead_B_minus_A_min"] = leads
    return res


# ------------------------------------------------------------------------------------------- city driver

def city_lags(cfg, live):
    awc = rdm.world_awc(cfg["city"], cfg["icao"])
    lag_m = float(np.median((awc.receipt - awc.index.to_series()).dt.total_seconds() / 60))
    if cfg["city"] == "Singapore":
        lag_d = rdm.nea_lag()[0]
    elif live is not None and len(live):
        lv = live if cfg["city"] != "Toronto" else live[live.index.minute == 0]
        lag_d = float(np.median((lv.receipt - lv.index.to_series()).dt.total_seconds() / 60))
    else:
        lag_d = None
    return lag_m, lag_d


def select_model(train, step, cad, lag_m, lag_d, workers):
    """Walk-forward inside the training days: fit on the first 70 %, score A and B log-loss at hourly decisions on the
    rest, for {one-scale, two-scale OU} x {plain mean, wiggle-shrunk mean}. The pick minimises the mean of A and B, so
    the choice favours neither arm."""
    n = len(train)
    inner_tr, inner_va = train[: int(0.7 * n)], train[int(0.7 * n):]
    scores = {}
    for shrink in (False, True):
        e2 = estimate(inner_tr, step, cad, lag_m, lag_d, shrink=shrink)
        days = [make_day(r, e2["mean"], e2["b_block"]) for r in inner_va]
        for name in ("one", "two"):
            lat = latent_from(e2["ou_one"] if name == "one" else e2["ou_two"], e2["drift"])
            jobs = [(d, lat, e2["noise"], e2["lag_m"], decisions_for(r, SEL_DECISION_STEP), arm, 1)
                    for d, r in zip(days, inner_va) for arm in ("A", "B")]
            sdf = score_rows(run_jobs(jobs, workers), {d.label: d for d in days}, lambda d, t: 0)
            scores[f"{name}|{'shrunk' if shrink else 'plain'}"] = dict(
                A=r4(sdf[sdf.arm == "A"].ll.mean()), B=r4(sdf[sdf.arm == "B"].ll.mean()), n=int(len(sdf)), days=len(inner_va))
    pick = min(scores, key=lambda k: (scores[k]["A"] + scores[k]["B"]) / 2)
    return pick, scores


def run_city(cfg, workers):
    t_start = time.time()
    city, tz = cfg["city"], cfg["tz"]
    usable, meta, live = assemble(cfg)
    lag_m, lag_d = city_lags(cfg, live)
    if lag_d is None:
        return dict(city=city, skipped="no dense receipt clock")
    n = len(usable)
    if n < 20:
        return dict(city=city, skipped=f"only {n} usable days", meta=meta)
    train, test = usable[: int(TRAIN_FRAC * n)], usable[int(TRAIN_FRAC * n):]
    cad = 30 if len(meta["metar"]["routine_minutes"]) == 2 else 60
    pick, sel = select_model(train, meta["step"], cad, lag_m, lag_d, workers)
    scales, mean_kind = pick.split("|")
    est = estimate(train, meta["step"], cad, lag_m, lag_d, shrink=mean_kind == "shrunk")
    fit = est["ou_one"] if scales == "one" else est["ou_two"]
    lat = latent_from(fit, est["drift"])
    noise = est["noise"]
    # A0 on train days
    tr_days = [make_day(r, est["mean"], est["b_block"]) for r in train]
    a0_train = []
    for d, r in zip(tr_days, train):
        a0_train += ss.a0_targets(d, lag_m, decisions_for(r))
    a0 = ss.fit_a0(a0_train)
    te_days = [make_day(r, est["mean"], est["b_block"]) for r in test]
    decs = {r["date"]: decisions_for(r) for r in test}
    jobs = [(d, lat, noise, lag_m, decs[d.label], arm, 1) for d in te_days for arm in ARMS]
    rows = run_jobs(jobs, workers) + a0_rows(te_days, lag_m, decs, a0)
    lab = {d.label: d for d in te_days}
    by_date = {r["date"]: r for r in test}

    def tz_hour(dd, t0):
        return int((by_date[dd]["a"] + pd.Timedelta(minutes=t0)).tz_convert(tz).hour)
    sdf = score_rows(rows, lab, tz_hour)
    OUT.mkdir(exist_ok=True)
    sdf.to_csv(OUT / f"{city.lower()}_decisions.csv.gz", index=False, compression="gzip")
    res = dict(city=city, station=cfg["icao"], source=cfg["source"], tz=tz, meta=meta,
               split=dict(train_days=len(train), test_days=len(test), train_first=train[0]["date"], train_last=train[-1]["date"],
                          test_first=test[0]["date"], test_last=test[-1]["date"]),
               lags_min=dict(metar_awc_p50=r4(lag_m), dense_p50=r4(lag_d)),
               params=dict(b_block_3h=[r4(v) for v in est["b_block"]], noise_choice=est["noise_choice"],
                           sigma_gauss=r4(est["noise_fit_gauss"]["s1"]),
                           mixture=dict(s1=r4(est["noise_fit_mixture"]["s1"]), s2=r4(est["noise_fit_mixture"]["s2"]),
                                        pi=r4(est["noise_fit_mixture"]["pi"]), aic=r4(est["noise_fit_mixture"]["aic"]),
                                        aic_gauss=r4(est["noise_fit_gauss"]["aic"])),
                           dense_white_core_sd=r4(noise.s1), dense_white_outlier_sd=r4(noise.s2), dense_outlier_weight=r4(noise.pi),
                           drift=est["drift"], mean=est["mean_report"], n_pairs=est["n_pairs"],
                           ou_one={k: (r4(v) if isinstance(v, float) else v) for k, v in est["ou_one"].items()},
                           ou_two=None if est["ou_two"] is None else {k: (r4(v) if isinstance(v, float) else v) for k, v in est["ou_two"].items()},
                           selection=dict(picked=pick, inner_walk_forward=sel),
                           latent_used=dict(tau_s=r4(lat.tau_s), s_s=r4(math.sqrt(lat.s2_s)), tau_f=r4(lat.tau_f), s_f=r4(math.sqrt(lat.s2_f)),
                                            tau_e=r4(lat.tau_e), s_d=r4(math.sqrt(lat.sd2))),
                           a0={s: {str(k): [r4(v[0]), r4(v[1])] for k, v in a0[s].items()} for s in a0}),
               results=dict(noaa=summarize(sdf[sdf.era == "noaa"], "NOAA-era test days (headline)"),
                            wu=summarize(sdf[sdf.era == "wu"], "WU-era test days"),
                            all=summarize(sdf, "all VERIFIED test days")),
               runtime_s=round(time.time() - t_start, 1))
    res["verdict"] = verdict(res)
    (OUT / f"{city.lower()}.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def verdict(res):
    """B usable iff, on NOAA-era test days, mean log-loss B < A (day-blocked 95 % upper bound < 0) and B's
    high-confidence errors are no worse than A's (errors <= A errors and <= expected), with zero semantic violations."""
    out = {}
    noaa = res["results"]["noaa"]
    for side in ("high", "low"):
        pa = noaa.get("paired_day_block", {}).get(f"{side}|ll|B-A")
        ba, bb = noaa.get("by_arm_side", {}).get(f"A|{side}"), noaa.get("by_arm_side", {}).get(f"B|{side}")
        if not pa or not ba or not bb:
            out[side] = dict(verdict="NO_DATA")
            continue
        ll_better = pa["mean"] < 0
        ll_sig = pa["hi"] < 0
        fc_ok = (bb["false_certainty_rate_all"] <= ba["false_certainty_rate_all"])
        sem_ok = bb["semantic_violation_decisions"] == 0 and ba["semantic_violation_decisions"] == 0
        v = "B_USABLE" if (ll_better and ll_sig and fc_ok and sem_ok) else ("B_BETTER_NOT_SIGNIFICANT" if ll_better and fc_ok and sem_ok
                                                                             else "B_NOT_USABLE")
        out[side] = dict(verdict=v, ll_diff=pa, false_certainty_A=ba["false_certainty_rate_all"], false_certainty_B=bb["false_certainty_rate_all"],
                         hc95_A=ba["hc95"], hc95_B=bb["hc95"], semantic_ok=sem_ok)
    return out


SS_CITIES = ("Helsinki", "Munich", "Tokyo", "Singapore", "Toronto", "Warsaw", "Amsterdam", "Madrid")


def main():
    which = sys.argv[1:] or SS_CITIES
    workers = max(1, (os.cpu_count() or 4) - 2)
    cfgs = {c["city"]: c for c in rdm.CITIES}
    for city in which:
        cfg = cfgs[city]
        if city in ("Amsterdam", "Madrid"):
            reason = ("KNMI: 769 bracket files cover 8 days (operator cap 800 calls); no train/test split possible"
                      if city == "Amsterdam" else "no dense archive (AEMET public XML exposes 24 h only)")
            OUT.mkdir(exist_ok=True)
            (OUT / f"{city.lower()}.json").write_text(json.dumps(dict(city=city, skipped=reason), indent=1))
            print(city, "SKIPPED", reason, flush=True)
            continue
        r = run_city(cfg, workers)
        if "skipped" in r:
            print(city, "SKIPPED", r["skipped"], flush=True)
            continue
        print(city, "train/test", r["split"]["train_days"], r["split"]["test_days"], "picked", r["params"]["selection"]["picked"],
              "runtime", r["runtime_s"], "verdict", {s: v["verdict"] for s, v in r["verdict"].items()}, flush=True)


if __name__ == "__main__":
    main()
