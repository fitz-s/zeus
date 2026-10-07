"""Runner: dense national station vs settled value, per city (read-only; WORLD/FORECASTS opened mode=ro).

Inputs
  dense archive  ../daily_extreme_agreement/raw/*.csv.gz and ./raw/*.csv.gz (+ live WORLD prints merged where the
                 archive ends; value identity on the overlap is reported)
  METAR + SPECI  ./raw/metar_iem_<ICAO>.csv.gz (IEM ASOS, report_type 3+4); integer from the TT/dd body group
  truth          FORECASTS.settlement_outcomes authority='VERIFIED'; era from provenance_json.data_version
  receipts       WORLD.observation_prints fetched_at_utc (dense live channels, aviationweather_metar)
Outputs
  ./per_city/<city>.json, ./summary_table.md, ./summary.json
"""
from __future__ import annotations

import gzip
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import dense_model as dm

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
DAE = HERE.parent / "daily_extreme_agreement"
OUT = HERE / "per_city"
STATE = Path("/Users/leofitz/zeus/state")
WORLD_URI = f"file:{STATE}/zeus-world.db?mode=ro"
FORECASTS_URI = f"file:{STATE}/zeus-forecasts.db?mode=ro"
UTC = timezone.utc
LIVE_SINCE = "2026-07-16"
COVER = 0.8
TRAIN_FRAC = 0.7
DAY_RISK = 1e-3  # per-day false-floor budget for the model-implied margin

CITIES = [
    dict(city="Helsinki", icao="EFHK", tz="Europe/Helsinki", source="FMI fmisid 100968, 10-min",
         archive=DAE / "raw/fmi_efhk.csv.gz", live="fmi_airport_temperature", step=10),
    dict(city="Munich", icao="EDDM", tz="Europe/Berlin", source="DWD 01262 TT_10, 10-min",
         archive=DAE / "raw/dwd_eddm.csv.gz", live="dwd_cdc_temperature", step=10),
    dict(city="Warsaw", icao="EPWA", tz="Europe/Warsaw", source="IMGW synop 12375, hourly",
         archive=DAE / "raw/imgw_epwa.csv.gz", live="imgw_synop_temperature", step=60),
    dict(city="Amsterdam", icao="EHAM", tz="Europe/Amsterdam", source="KNMI 06240 ta, :20/:30/:50/:00 -> :25/:55 interp",
         archive=RAW / "knmi_06240_bracket.csv.gz", live=None, step=None),
    dict(city="Tokyo", icao="RJTT", tz="Asia/Tokyo", source="JMA AMeDAS 44166 Haneda, 10-min",
         archive=RAW / "jma_haneda_10min.csv.gz", live="jma_amedas_temperature", step=10),
    dict(city="Singapore", icao="WSSS", tz="Asia/Singapore", source="NEA S24 Changi, 1-min",
         archive=DAE / "raw/nea_s24_wsss.csv.gz", live=None, step=1),
    dict(city="Toronto", icao="CYYZ", tz="America/Toronto", source="ECCC 51459 hourly (live: SWOB)",
         archive=DAE / "raw/eccc_cyyz_hourly.csv.gz", live="eccc_swob_temperature", step=60),
    dict(city="Madrid", icao="LEMD", tz="Europe/Madrid", source="AEMET 3129 horario, hourly (2 x 24 h)",
         archive=None, live=None, step=60),
]


def ro(uri):
    c = sqlite3.connect(uri, uri=True, timeout=15)
    c.execute("pragma query_only=1")
    return c


def ts(s):
    d = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return pd.Timestamp(d if d.tzinfo else d.replace(tzinfo=UTC))


def q(v, p):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], float)
    return None if v.size == 0 else round(float(np.quantile(v, p)), 2)


def qs(v):
    return dm.quantiles(v)


def r3(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 4)


# ------------------------------------------------------------------------------------------------ loaders

def read_series(path, col="value"):
    df = pd.read_csv(path, compression="gzip")
    s = pd.Series(df[col].astype(float).values, index=pd.to_datetime(df["ts_utc"], utc=True))
    return s[~s.index.duplicated(keep="last")].sort_index().dropna()


def world_dense(city, icao, ch):
    """{instant: (value at first receipt, first receipt)} for a live dense channel."""
    con = ro(WORLD_URI)
    rows = con.execute("select publish_ts_utc, value_native, fetched_at_utc from observation_prints where city=? and station_id=? "
                       "and source_channel=? and publish_ts_utc>=?", (city, icao, ch, LIVE_SINCE)).fetchall()
    con.close()
    best = {}
    for p, v, f in rows:
        t, r = ts(p).floor("min"), ts(f)
        if t not in best or r < best[t][1]:
            best[t] = (float(v), r)
    df = pd.DataFrame([(t, v, r) for t, (v, r) in best.items()], columns=["t", "x", "receipt"]).set_index("t").sort_index()
    return df


def metar_instant(raw, fetched):
    m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", raw or "")
    if not m:
        return None
    dd, hh, mm = map(int, m.groups())
    f = fetched.to_pydatetime()
    for k in (0, -1):
        mo = (f.replace(day=1) + timedelta(days=32 * k)).replace(day=1)
        try:
            c = mo.replace(day=dd, hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            continue
        if timedelta(0) <= f - c <= timedelta(days=2):
            return pd.Timestamp(c)
    return None


def world_awc(city, icao):
    con = ro(WORLD_URI)
    rows = con.execute("select raw_report, value_native, fetched_at_utc from observation_prints where city=? and station_id=? "
                       "and source_channel='aviationweather_metar' and publish_ts_utc>=?", (city, icao, LIVE_SINCE)).fetchall()
    con.close()
    best = {}
    for raw, v, f in rows:
        r = ts(f)
        t = metar_instant(raw, r)
        if t is not None and (t not in best or r < best[t][1]):
            best[t] = (int(round(float(v))), r)
    return pd.DataFrame([(t, v, r) for t, (v, r) in best.items()], columns=["t", "M", "receipt"]).set_index("t").sort_index()


TT = re.compile(r"(?<=\s)(M?\d{2})/(M?\d{2}|//)?(?=\s|$)")


def load_metar(icao):
    rows, m00 = [], 0
    for line in gzip.open(RAW / f"metar_iem_{icao}.csv.gz", "rt").read().splitlines()[1:]:
        _, valid, body = line.split(",", 2)
        m = TT.search(" " + body + " ")
        if not m:
            continue
        tt = m.group(1)
        m00 += tt == "M00"
        rows.append((pd.Timestamp(valid, tz="UTC"), -int(tt[1:]) if tt.startswith("M") else int(tt)))
    df = pd.DataFrame(rows, columns=["t", "M"]).drop_duplicates("t", keep="last").set_index("t").sort_index()
    mins = Counter(df.index.minute).most_common(2)
    k = 2 if len(mins) == 2 and mins[1][1] > 0.4 * mins[0][1] else 1
    routine = sorted(m for m, _ in mins[:k])
    df["routine"] = df.index.minute.isin(routine)
    return df, dict(n=len(df), n_routine=int(df.routine.sum()), n_speci=int((~df.routine).sum()), routine_minutes=routine,
                    first=str(df.index.min()), last=str(df.index.max()), m00=m00)


def load_truth(city):
    con = ro(FORECASTS_URI)
    rows = con.execute("select target_date, temperature_metric, settlement_value, json_extract(provenance_json,'$.data_version') "
                       "from settlement_outcomes where city=? and authority='VERIFIED' and settlement_value is not null", (city,)).fetchall()
    con.close()
    out = {"high": {}, "low": {}}
    for d, m, v, dv in rows:
        era = "noaa" if dv == "noaa_wrh_timeseries_v1" else ("wu" if dv and dv.startswith("wu_") else f"other:{dv}")
        out[m][d] = (dm.R(float(v)), era)
    return out


def load_aemet():
    rows = {}
    for p in sorted(list(DAE.glob("raw/aemet_lemd_horario_*.xml.gz")) + list(RAW.glob("aemet_lemd_horario_*.xml.gz"))):
        est = ET.fromstring(gzip.open(p).read()).find("estacion")
        assert est.get("id_c") == "3129", est.attrib
        for per in est.findall("periodo"):
            v = per.find("temperatura")
            if v is not None and v.text:
                rows[pd.Timestamp(per.get("utc"), tz="UTC")] = float(v.text)
    return pd.Series(rows).sort_index()


def load_dense(cfg):
    """Returns (series on its grid, identity report, live frame or None)."""
    ident, live = {}, None
    if cfg["city"] == "Madrid":
        return load_aemet(), dict(note="AEMET public XML 'horario' captures only (no archive; keyed OpenData unavailable)"), None
    s = read_series(cfg["archive"], "ta" if cfg["city"] == "Amsterdam" else "value")
    if cfg["live"]:
        live = world_dense(cfg["city"], cfg["icao"], cfg["live"])
        if cfg["city"] != "Toronto":  # SWOB is a different product from the ECCC hourly archive: latency only
            ov = s.index.intersection(live.index)
            eq = int(np.sum(np.isclose(s.reindex(ov).values, live.x.reindex(ov).values, atol=0.051)))
            ident = dict(live_channel=cfg["live"], overlap=len(ov), equal_within_0p05=eq,
                         archive_last=str(s.index.max()), live_rows_appended=int((live.index > s.index.max()).sum()))
            s = pd.concat([s, live.x[live.index > s.index.max()]]).sort_index()
        else:
            ov = s.index.intersection(live.index)
            eq = int(np.sum(np.isclose(s.reindex(ov).values, live.x.reindex(ov).values, atol=0.051)))
            ident = dict(live_channel=cfg["live"], overlap=len(ov), equal_within_0p05=eq, note="SWOB not merged (latency only)")
    return s, ident, live


# ------------------------------------------------------------------------------------------------ helpers

def local_day(idx, tz):
    return np.asarray(pd.DatetimeIndex(idx).tz_convert(tz).strftime("%Y-%m-%d"))


def day_minutes(d, tz):
    z = ZoneInfo(tz)
    a = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=z)
    b = datetime.combine(date.fromisoformat(d) + timedelta(days=1), datetime.min.time(), tzinfo=z)
    return (b - a).total_seconds() / 60


def settlement_frame(cfg, dense, metar, tz):
    """S instants with the dense value at the instant (xS). Amsterdam: midpoint interpolation of the bracketing stamps,
    plus the rigorous bracket [tn, tx] of the 10-min interval that contains the instant."""
    S = metar.copy()
    S["day"] = local_day(S.index, tz)
    if cfg["city"] == "Amsterdam":
        br = pd.read_csv(cfg["archive"], compression="gzip")
        br.index = pd.to_datetime(br.ts_utc, utc=True)
        a, b = S.index - pd.Timedelta(minutes=5), S.index + pd.Timedelta(minutes=5)
        S["xS"] = (br.ta.reindex(a).values + br.ta.reindex(b).values) / 2
        S["tn"], S["tx"] = br.tn.reindex(b).values, br.tx.reindex(b).values
        S["x_lo"], S["x_hi"] = br.ta.reindex(a).values, br.ta.reindex(b).values
    else:
        S["xS"] = dense.reindex(S.index).values
    return S


# ------------------------------------------------------------------------------------------------ 1. measurement

def measurement(cfg, S, dense, tz):
    P = S.dropna(subset=["xS"])
    k, x = P.M.values.astype(float), P.xS.values
    g = dm.fit_icm(k, x)
    b, s = g["beta"][0], g["sigma"]
    t = dm.fit_icm_t(k, x)
    hours = pd.DatetimeIndex(P.index).tz_convert(tz).hour
    dayt = (hours >= 9) & (hours < 18)
    sub = {}
    for name, m in (("day_09_18", dayt), ("night", ~dayt), ("routine", P.routine.values), ("speci", ~P.routine.values)):
        if m.sum() >= 30:
            f = dm.fit_icm(k[m], x[m])
            sub[name] = dict(n=int(m.sum()), b=r3(f["beta"][0]), sigma=r3(f["sigma"]))
    days = np.unique(P.day)
    half = np.isin(P.day, days[: len(days) // 2])
    for name, m in (("first_half_days", half), ("second_half_days", ~half)):
        if m.sum() >= 30:
            f = dm.fit_icm(k[m], x[m])
            sub[name] = dict(n=int(m.sum()), b=r3(f["beta"][0]), sigma=r3(f["sigma"]))
    # residual autocorrelation between consecutive settlement instants (routine, one cadence apart)
    R_ = P[P.routine]
    cad = pd.Timedelta(minutes=30 if len(metar_minutes(S)) == 2 else 60)
    nxt = R_.reindex(R_.index + cad)
    ok = ~np.isnan(nxt.xS.values)
    lo1, hi1 = (R_.M.values - 0.5 - R_.xS.values - b) / s, (R_.M.values + 0.5 - R_.xS.values - b) / s
    lo2, hi2 = (nxt.M.values - 0.5 - nxt.xS.values - b) / s, (nxt.M.values + 0.5 - nxt.xS.values - b) / s
    rho = dm.fit_rho(lo1[ok], hi1[ok], lo2[ok], hi2[ok]) if ok.sum() > 30 else None
    gr = dm.generalized_residual(R_.M.values, R_.xS.values + b, s)
    gr2 = dm.generalized_residual(nxt.M.values, nxt.xS.values + b, s)
    rho_m = float(np.corrcoef(gr[ok], gr2[ok])[0, 1]) if ok.sum() > 30 else None
    # timestamp alignment check: same fit with the dense series shifted by L minutes
    lag = {}
    if cfg["step"] in (1, 10):
        lags = range(-5, 6) if cfg["step"] == 1 else (-20, -10, 0, 10, 20)
        for L in lags:
            xl = dense.reindex(P.index + pd.Timedelta(minutes=L)).values
            mm = ~np.isnan(xl)
            if mm.sum() > 100:
                f = dm.fit_icm(k[mm], xl[mm])
                lag[str(L)] = dict(n=int(mm.sum()), b=r3(f["beta"][0]), sigma=r3(f["sigma"]), nll_per_pair=r3(f["nll"] / mm.sum()))
    out = dict(n_pairs=int(len(P)), n_pairs_routine=int(P.routine.sum()), n_pairs_speci=int((~P.routine).sum()),
               n_days=int(len(days)), first=str(P.index.min()), last=str(P.index.max()),
               gauss=dict(b=r3(b), sigma=r3(s), se_b=r3(g["se_beta"][0]), se_sigma=r3(g["se_sigma"]), nll=r3(g["nll"]),
                          aic=r3(g["aic"]), at_sigma_bound=g["at_sigma_bound"]),
               student_t=dict(b=r3(t["beta"][0]), sigma=r3(t["sigma"]), nu=t["nu"], nll=r3(t["nll"]), aic=r3(t["aic"]),
                              nu_profile_nll=t["nu_profile_nll"]),
               preferred_by_aic="student_t" if t["aic"] < g["aic"] - 2 else "gauss",
               offset_table_gauss=dm.offset_table(k, x, b, s),
               offset_table_t=dm.offset_table(k, x, t["beta"][0], t["sigma"], "t", t["nu"]),
               raw_agreement=dict(R_x_eq_M=int(np.sum(dm.R(x) == k)), share=r3(np.mean(dm.R(x) == k)),
                                  R_xb_eq_M=int(np.sum(dm.R(x + b) == k)), share_b=r3(np.mean(dm.R(x + b) == k)),
                                  sd_M_minus_x=r3(np.std(k - x))),
               subsets=sub, residual_autocorr=dict(latent_rho_pairwise=rho, generalized_residual_lag1=r3(rho_m),
                                                    n_consecutive=int(ok.sum()), cadence_min=int(cad.total_seconds() // 60)),
               lag_alignment=lag)
    return out, b, s, t


def metar_minutes(S):
    return sorted(set(S.index[S.routine].minute))


# ------------------------------------------------------------------------------------------------ 2. hard floor

def side_arrays(S, truth, side, b, xcol="xS"):
    P = S.dropna(subset=[xcol])
    P = P[P.day.isin(list(truth[side]))]
    T = np.array([truth[side][d][0] for d in P.day])
    era = np.array([truth[side][d][1] for d in P.day])
    return P, P[xcol].values + b, T, era


def first_time(times, mask):
    return times[mask].min() if mask.any() else None


def floor_side(S, truth, side, b, s, tfit, awc, dense_lag, live_rx, xcol="xS"):
    from scipy import stats
    P, xb, T, era = side_arrays(S, truth, side, b, xcol)
    if len(P) == 0:
        return dict(n_days=0)
    day = P.day.values
    M = P.M.values
    c_star = dm.critical_margin(xb, T, side)
    m_c = dm.min_zero_false_margin(day, xb, T, side, m_max=10.0)
    ci = (xb - M - 0.5) if side == "high" else (M - 0.5 - xb)
    m_inst = float(np.round(np.ceil((ci.max() + (1e-9 if side == "high" else 0)) / 0.05) * 0.05, 2))
    j = int(np.argmax((xb - T - 0.5) if side == "high" else (T - 0.5 - xb)))
    by_era = {}
    for e in sorted(set(era)):
        m = era == e
        by_era[e] = dict(days=int(len(set(day[m]))), critical=r3(dm.critical_margin(xb[m], T[m], side)),
                         m_zero_false=dm.min_zero_false_margin(day[m], xb[m], T[m], side, m_max=10.0))
    # out-of-sample: margin from the first 70 % of days, false floors counted on the rest
    days = np.unique(day)
    cut = days[int(TRAIN_FRAC * len(days))] if len(days) > 3 else None
    oos = None
    if cut is not None:
        tr, te = day < cut, day >= cut
        m_tr = dm.min_zero_false_margin(day[tr], xb[tr], T[tr], side, m_max=10.0)
        oos = dict(train_days=int(len(set(day[tr]))), test_days=int(len(set(day[te]))), m_train=m_tr,
                   test_false_days=dm.false_days(day[te], xb[te], T[te], m_tr, side) if m_tr is not None else None,
                   test_critical=r3(dm.critical_margin(xb[te], T[te], side)))
    n_per_day = float(np.median(pd.Series(day).value_counts().values))
    m_model = dict(per_day_risk=DAY_RISK, instants_per_day=n_per_day,
                   gauss=r3(s * stats.norm.isf(DAY_RISK / n_per_day)),
                   student_t=r3(tfit["sigma"] * stats.t.isf(DAY_RISK / n_per_day, tfit["nu"])))
    res = dict(n_days=int(len(days)), n_instants=int(len(P)), critical_margin=r3(c_star),
               rule="zero false iff m > c*" if side == "high" else "zero false iff m >= c*",
               m_c=m_c, false_at_m0=dict(days=dm.false_days(day, xb, T, 0.0, side), instants=int(dm.false_mask(xb, T, 0.0, side).sum())),
               false_at_m_c=dict(days=dm.false_days(day, xb, T, m_c, side), instants=int(dm.false_mask(xb, T, m_c, side).sum())),
               binding_instant=dict(t=str(P.index[j]), day=day[j], x=r3(P[xcol].values[j]), M=int(M[j]), settled=int(T[j]), era=era[j]),
               instant_level=dict(critical=r3(float(ci.max())), m_zero_false_vs_same_instant_metar=m_inst,
                                  false_instants_at_m0=int((ci >= 0).sum()) if side == "high" else int((ci > 0).sum())),
               by_era=by_era, out_of_sample=oos, model_implied_margin=m_model)
    for label, m in (("m_c", m_c), ("m_inst", max(m_inst, m_c))):
        res[f"fires_{label}"] = fires(P, xb, T, side, m, awc, dense_lag, live_rx, xcol)
    return res


def fires(P, xb, T, side, m, awc, dense_lag, live_rx, xcol):
    """Floor (high) / ceiling (low) at margin m: share of days that reach the settled value, and leads vs METAR."""
    F = dm.floor_high(xb, m) if side == "high" else dm.ceil_low(xb, m)
    reach = (lambda v, k: v >= k) if side == "high" else (lambda v, k: v <= k)
    P = P.assign(F=F, settled=T)
    final_days, lead_inst, lead_model, lead_live, cross_inst = 0, [], [], [], []
    n = 0
    for d, g in P.groupby("day"):
        n += 1
        k = int(g.settled.iloc[0])
        times = g.index
        tf = first_time(times, reach(g.F.values, k))
        tm = first_time(times, reach(g.M.values, k))  # same-grid METAR
        final_days += tf is not None
        if tf is not None and tm is not None:
            lead_inst.append((tm - tf).total_seconds() / 60)
        # all thresholds the day crosses after its first on-grid METAR
        k0 = int(g.M.iloc[0])
        ks = range(k0 + 1, k + 1) if side == "high" else range(k0 - 1, k - 1, -1)
        for kk in ks:
            a, c = first_time(times, reach(g.F.values, kk)), first_time(times, reach(g.M.values, kk))
            if a is not None and c is not None:
                cross_inst.append((c - a).total_seconds() / 60)
        # receipt leads: AWC actual receipt of the first METAR/SPECI reaching k vs dense receipt (live or modeled)
        if awc is not None and len(awc):
            aw = awc[awc.day.values == d]
            ta = first_time(aw.receipt.values, reach(aw.M.values, k)) if len(aw) else None
            if ta is not None:
                ta = utc(ta)
            if ta is not None and tf is not None and dense_lag is not None:
                lead_model.append((ta - (tf + pd.Timedelta(minutes=dense_lag))).total_seconds() / 60)
            if ta is not None and live_rx is not None:
                rx = live_rx.reindex(times[reach(g.F.values, k)]).dropna()
                if len(rx):
                    lead_live.append((ta - utc(rx.min())).total_seconds() / 60)
    return dict(margin=m, days=n, days_floor_reaches_settled=final_days, share=r3(final_days / n if n else None),
                lead_vs_same_grid_metar_instant_min=qs(lead_inst), threshold_crossings_instant_lead_min=qs(cross_inst),
                lead_vs_awc_receipt_modeled_min=qs(lead_model), lead_vs_awc_receipt_live_min=qs(lead_live))


def utc(x):
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t


# ------------------------------------------------------------------------------------------------ 3. nowcast

def nowcast(cfg, S, dense, b, tz, live_rx, awc):
    if cfg["city"] == "Amsterdam":
        br = pd.read_csv(cfg["archive"], compression="gzip")
        dense = pd.Series(br.ta.values, index=pd.to_datetime(br.ts_utc, utc=True)).dropna()
    targets = S[S.routine]
    allS = S.M
    deltas = (5, 25) if cfg["city"] == "Amsterdam" else (10, 20, 30)
    out = {}
    didx = dense.index
    for D in deltas:
        t1 = targets.index
        t0 = t1 - pd.Timedelta(minutes=D)
        x0 = dense.reindex(t0).values
        # trend: latest earlier dense stamp within 30 min, normalised to deg C per 10 min
        pos = didx.searchsorted(t0, side="left") - 1
        prev_t = didx[np.clip(pos, 0, len(didx) - 1)]
        gap = (t0 - prev_t).total_seconds().values / 60
        xp = dense.values[np.clip(pos, 0, len(didx) - 1)]
        trend = np.where((pos >= 0) & (gap > 0) & (gap <= 30), (x0 - xp) / np.where(gap > 0, gap, 1) * 10, np.nan)
        # previous METAR/SPECI integer observed at or before t0 (within 90 min)
        sp = allS.index.searchsorted(t0, side="right") - 1
        ok_sp = sp >= 0
        tprev = allS.index[np.clip(sp, 0, len(allS) - 1)]
        mprev = np.where(ok_sp & ((t0 - tprev).total_seconds().values <= 5400), allS.values[np.clip(sp, 0, len(allS) - 1)], np.nan)
        df = pd.DataFrame(dict(t0=pd.Series(t0, index=t1), x0=x0, trend=trend, mprev=mprev, M=targets.M.values, day=local_day(t1, tz)), index=t1).dropna()
        if len(df) < 50:
            out[str(D)] = dict(n=int(len(df)), note="insufficient pairs (grid does not contain t1 - delta)")
            continue
        days = np.unique(df.day)
        cut = days[int(TRAIN_FRAC * len(days))]
        tr, te = df[df.day < cut], df[df.day >= cut]
        res = dict(t0_in_settlement_set=bool(np.isin(te.t0.values, S.index.values).mean() > 0.5), n_train=int(len(tr)), n_test=int(len(te)),
                   train_days=int((days < cut).sum()), test_days=int((days >= cut).sum()), test_first_day=str(cut))
        K = te.mprev.values[:, None].astype(int) + np.arange(-6, 7)[None, :]
        obs = te.M.values.astype(int)
        probs = {}
        probs["naive_prev_metar"] = (K == te.mprev.values[:, None]).astype(float)
        jtr = (tr.M - tr.mprev).astype(int)
        cnt = Counter(jtr)
        pj = np.array([cnt.get(j, 0) + 0.5 for j in range(-6, 7)], float)
        probs["persistence_prob"] = np.tile(pj / pj.sum(), (len(te), 1))
        # empirical dense: j = M - R(x0 + b) by position u of x0 + b inside its rounding cell
        def ubin(x):
            v = x + b
            u = v - dm.R(v)
            return np.clip(((u + 0.5) * 5).astype(int), 0, 4)
        base_tr, base_te = dm.R(tr.x0.values + b), dm.R(te.x0.values + b)
        jd = tr.M.values - base_tr
        ub_tr, ub_te = ubin(tr.x0.values), ubin(te.x0.values)
        table = np.full((5, 13), 0.5)
        for u_, j_ in zip(ub_tr, jd):
            if -6 <= j_ <= 6:
                table[u_, j_ + 6] += 1
        table /= table.sum(1, keepdims=True)
        Pe = np.zeros_like(K, float)
        for i in range(len(te)):
            for jj in range(13):
                kk = base_te[i] + jj - 6
                w = np.where(K[i] == kk)[0]
                if w.size:
                    Pe[i, w[0]] += table[ub_te[i], jj]
        probs["empirical_dense"] = Pe
        fits = {}
        for name, cols in (("icm_dense", []), ("icm_dense_trend", ["trend"]), ("icm_dense_trend_prevmetar", ["trend", "gap_prev"])):
            def X(d):
                c = [np.ones(len(d))]
                for cc in cols:
                    c.append(d.trend.values if cc == "trend" else (d.mprev.values - d.x0.values))
                return np.column_stack(c)
            f = dm.fit_icm(tr.M.values, tr.x0.values, X(tr))
            mu = te.x0.values + X(te) @ np.array(f["beta"])
            probs[name] = dm.pmf_rows(mu, f["sigma"], K)
            fits[name] = dict(beta=[r3(v) for v in f["beta"]], sigma=r3(f["sigma"]), covariates=["1"] + cols)
        scores = {n_: dict(brier=r3(dm.brier_multi(Pm, K, obs)), log_loss=r3(dm.log_loss(Pm, K, obs)),
                           accuracy=r3(np.mean(K[np.arange(len(te)), Pm.argmax(1)] == obs))) for n_, Pm in probs.items()}
        res.update(fits=fits, scores=scores,
                   reliability=dict(icm_dense_trend=dm.reliability(probs["icm_dense_trend"], K, obs),
                                    empirical_dense=dm.reliability(probs["empirical_dense"], K, obs),
                                    persistence_prob=dm.reliability(probs["persistence_prob"], K, obs)),
                   brier_ratio_icm_trend_vs_naive=r3(scores["icm_dense_trend"]["brier"] / scores["naive_prev_metar"]["brier"]),
                   brier_ratio_icm_trend_vs_persistence=r3(scores["icm_dense_trend"]["brier"] / scores["persistence_prob"]["brier"]))
        # (ii) availability of x(t0) before the AWC METAR for t1 arrives (live receipts)
        if awc is not None and len(awc):
            a_rx = awc.receipt.reindex(df.index)
            if live_rx is not None:
                d_rx = live_rx.reindex(pd.DatetimeIndex(df.t0))
                v = (a_rx.values - d_rx.values)
                res["live_minutes_x_t0_before_awc_t1"] = qs([x / np.timedelta64(1, "m") for x in v if not pd.isna(x)])
            t0_lag = cfg.get("pub_lag", cfg.get("lag_model"))
            if t0_lag is not None:
                v = (a_rx.values - (df.t0.values + np.timedelta64(int(t0_lag * 60), "s")))
                res["modeled_minutes_x_t0_before_awc_t1"] = qs([x / np.timedelta64(1, "m") for x in v if not pd.isna(x)])
        out[str(D)] = res
    return out


# ------------------------------------------------------------------------------------------------ 4. gap decomposition

def gaps(cfg, S, dense, truth, tz):
    step_per_day = None if cfg["city"] == "Amsterdam" else cfg["step"]
    if cfg["city"] == "Amsterdam":
        br = pd.read_csv(cfg["archive"], compression="gzip")
        dense = pd.Series(br.ta.values, index=pd.to_datetime(br.ts_utc, utc=True)).dropna()
    G = pd.DataFrame(dict(x=dense.values, day=local_day(dense.index, tz)), index=dense.index)
    gd = G.groupby("day").x.agg(["max", "min", "count"])
    sd = S.groupby("day")
    rmin = metar_minutes(S)
    out = {}
    for side in ("high", "low"):
        agg = np.max if side == "high" else np.min
        cnt = Counter()
        hist = {k: Counter() for k in ("a", "b", "c", "d", "total")}
        speci_sets, offgrid_sets, n, era_n = 0, 0, 0, Counter()
        inst_b = Counter()
        for d, (H, era) in sorted(truth[side].items()):
            if d not in gd.index or d not in sd.groups:
                continue
            s = sd.get_group(d)
            mins = day_minutes(d, tz)
            exp_g = mins / (15 if cfg["city"] == "Amsterdam" else step_per_day)
            exp_r = mins / (60 / len(rmin))
            sg = s.dropna(subset=["xS"])
            if gd.loc[d, "count"] < COVER * exp_g or s.routine.sum() < COVER * exp_r or sg.routine.sum() < COVER * exp_r * (
                    0.5 if cfg["step"] == 60 and len(rmin) == 2 else 1):
                continue
            n += 1
            era_n[era] += 1
            A_full = dm.R(gd.loc[d, "max" if side == "high" else "min"])
            A_S = dm.R(agg(sg.xS.values))
            B = int(agg(sg.M.values))
            C = int(agg(s.M.values))
            terms = dict(a=A_full - A_S, b=A_S - B, c=B - C, d=C - H, total=A_full - H)
            for k_, v in terms.items():
                hist[k_][int(v)] += 1
                cnt[k_] += v != 0
            cnt[f"d_{era}"] += terms["d"] != 0
            inst_b["pairs"] += len(sg)
            inst_b["mismatch"] += int(np.sum(dm.R(sg.xS.values) != sg.M.values))
            ext = s.M.values == C
            if not s.routine.values[ext].any():
                speci_sets += 1
            if np.isnan(s.xS.values[ext]).all():
                offgrid_sets += 1
        out[side] = dict(days=n, days_by_era=dict(era_n), nonzero_days=dict(a_cadence=cnt["a"], b_measurement=cnt["b"],
                                                                            c_coverage=cnt["c"], d_era=cnt["d"],
                                                                            d_era_wu=cnt["d_wu"], d_era_noaa=cnt["d_noaa"],
                                                                            total=cnt["total"]),
                         hist={k: {str(i): c for i, c in sorted(h.items())} for k, h in hist.items()},
                         instant_measurement=dict(pairs=inst_b["pairs"], R_x_ne_M=inst_b["mismatch"]),
                         speci_only_sets_extreme_days=speci_sets, offgrid_only_sets_extreme_days=offgrid_sets)
    out["definition"] = ("A_full=R(dense day extreme over G), A_S=R(extreme over S∩G of x), B=extreme over S∩G of METAR, "
                         "C=extreme over all S (METAR+SPECI), H=settled; a=A_full-A_S, b=A_S-B, c=B-C, d=C-H, a+b+c+d=A_full-H")
    return out


# ------------------------------------------------------------------------------------------------ latency

def latency(cfg, S, awc, live, tz):
    out = {}
    if awc is not None and len(awc):
        lag = (awc.receipt - awc.index.to_series()).dt.total_seconds() / 60
        out["awc_lag_min"] = qs(lag.values)
    if live is not None and len(live):
        lag = (live.receipt - live.index.to_series()).dt.total_seconds() / 60
        out["dense_live_lag_min"] = qs(lag.values)
        ov = awc.index.intersection(live.index)
        lead = (awc.receipt.reindex(ov) - live.receipt.reindex(ov)).dt.total_seconds() / 60
        out["i_same_instant_awc_minus_dense_receipt_min"] = qs(lead.values)
        out["i_window"] = [str(ov.min()) if len(ov) else None, str(ov.max()) if len(ov) else None]
    if cfg.get("lag_model") is not None:
        out["dense_lag_model_min"] = cfg["lag_model"]
        out["lag_model_source"] = cfg["lag_model_source"]
        if awc is not None and len(awc):
            alag = (awc.receipt - awc.index.to_series()).dt.total_seconds() / 60
            out["i_modeled_awc_minus_dense_min"] = qs((alag - cfg["lag_model"]).values)
    return out


# ------------------------------------------------------------------------------------------------ main

def nea_lag():
    p = RAW / "nea_s24_live_probe.json"
    if not p.exists():
        return None, None
    d = json.loads(p.read_text())
    lags = [(ts(v["receipt"]) - ts(k)).total_seconds() / 60 for k, v in d["first_seen"].items()]
    return (round(float(np.median(lags)), 2) if lags else None), dict(n=len(lags), q=qs(lags), polls=len(d["polls"]), every_s=d["every_s"],
                                                                         errors=len(d["errors"]), note="v1 latest endpoint carries one 5-min stamp per poll")


def knmi_lag():
    p = RAW / "knmi_publication_lag.json"
    d = json.loads(p.read_text())
    return d["lag_min_p50"], {k: d[k] for k in ("probe_at", "n", "lag_min_p10", "lag_min_p50", "lag_min_p90")}


def interpolation_error(dense_1min):
    """Midpoint linear-interpolation error of a 10-min bracket, from the 1-min NEA series (x(t) - mean(x(t-5), x(t+5)))."""
    s = dense_1min
    a, c = s.reindex(s.index - pd.Timedelta(minutes=5)).values, s.reindex(s.index + pd.Timedelta(minutes=5)).values
    e = s.values - (a + c) / 2
    e = e[np.isfinite(e)]
    return dict(n=int(e.size), sd=r3(np.std(e)), mad_sd=r3(1.4826 * np.median(np.abs(e - np.median(e)))), p99_abs=r3(np.quantile(np.abs(e), 0.99)))


def verdict(meas, fl, nc, lat):
    """FLOOR_USABLE: zero false floors at m_c over all history AND on the held-out 30 % (margin from the first 70 %),
    the floor reaches the settled value on >= 20 % of days, its lead over the AWC receipt is positive at the median, and
    the dense channel's receipt of a settlement instant beats AWC's receipt of the same METAR (live n >= 30, else the
    modeled lag). NOWCAST_ONLY: a dense nowcast from a non-settlement instant beats both baselines on the held-out split
    and x(t0) is available before the AWC METAR at t1. Otherwise NOT_USEFUL."""
    reasons = []
    usable_side = []
    same = lat.get("i_same_instant_awc_minus_dense_receipt_min") or {}
    same_ok = (same.get("p50") or -1) > 0 if same.get("n", 0) >= 30 else (lat.get("i_modeled_awc_minus_dense_min") or {}).get("p50", -1) > 0
    for side in ("high", "low"):
        f = fl.get(side, {})
        if not f.get("n_days"):
            continue
        fr = f["fires_m_c"]
        oos = f.get("out_of_sample") or {}
        lead = fr["lead_vs_awc_receipt_live_min"] if fr["lead_vs_awc_receipt_live_min"].get("n", 0) >= 5 else fr["lead_vs_awc_receipt_modeled_min"]
        ok = (f["false_at_m_c"]["days"] == 0 and (fr["share"] or 0) >= 0.2 and (oos.get("test_false_days") == 0)
              and lead.get("n", 0) >= 3 and (lead.get("p50") or -1) > 0 and same_ok)
        reasons.append(f"{side}: m_c={f['m_c']} fires {fr['days_floor_reaches_settled']}/{fr['days']} d, OOS false={oos.get('test_false_days')}, "
                       f"floor lead p50={lead.get('p50')} (n={lead.get('n', 0)}), same-instant receipt lead p50="
                       f"{same.get('p50', (lat.get('i_modeled_awc_minus_dense_min') or {}).get('p50'))}")
        if ok:
            usable_side.append(side)
    if usable_side:
        return "FLOOR_USABLE", f"floor usable on {'/'.join(usable_side)}; " + "; ".join(reasons)
    better = []
    for D, r in nc.items():
        if "scores" not in r:
            continue
        sc = r["scores"]
        best = min(("icm_dense_trend", "icm_dense", "empirical_dense"), key=lambda k: sc[k]["brier"])
        avail = r.get("live_minutes_x_t0_before_awc_t1") or r.get("modeled_minutes_x_t0_before_awc_t1") or {}
        if sc[best]["brier"] < min(sc["naive_prev_metar"]["brier"], sc["persistence_prob"]["brier"]) and (avail.get("p50") or -1) > 0 \
                and not r["t0_in_settlement_set"]:
            better.append(f"d={D}: {best} Brier {sc[best]['brier']} vs naive {sc['naive_prev_metar']['brier']} / persistence "
                          f"{sc['persistence_prob']['brier']}, x(t0) {avail.get('p50')} min before AWC t1")
    if better:
        return "NOWCAST_ONLY", "; ".join(better) + " | floor: " + "; ".join(reasons)
    return "NOT_USEFUL", "floor: " + "; ".join(reasons) + " | nowcast: no held-out Brier gain available before the AWC METAR"


def run(cfg, extras):
    city, icao, tz = cfg["city"], cfg["icao"], cfg["tz"]
    dense, ident, live = load_dense(cfg)
    metar, minfo = load_metar(icao)
    truth = load_truth(city)
    awc = world_awc(city, icao)
    S = settlement_frame(cfg, dense, metar, tz)
    meas, b, s, tfit = measurement(cfg, S, dense, tz)
    live_rx = live.receipt if (live is not None and city != "Toronto") else None
    if city == "Toronto" and live is not None:
        live_rx = live.receipt[live.index.minute == 0]
    lag_model = cfg.get("lag_model")
    if lag_model is None and live is not None and len(live):
        lag_model = float(np.median((live.receipt - live.index.to_series()).dt.total_seconds() / 60))
    awc_d = awc.assign(day=local_day(awc.index, tz))
    fl = {}
    for side in ("high", "low"):
        fl[side] = floor_side(S, truth, side, b, s, tfit, awc_d, lag_model, live_rx)
    if city == "Amsterdam":  # rigorous bracket variant: tn (high) / tx (low) of the 10-min interval containing the instant
        S2 = S.copy()
        fl["bracket_variant"] = {}
        for side, col in (("high", "tn"), ("low", "tx")):
            S2["xB"] = S2[col]
            fl["bracket_variant"][side] = floor_side(S2, truth, side, b, s, tfit, awc_d, lag_model, None, xcol="xB")
    nc = nowcast(dict(cfg, lag_model=lag_model), S, dense, b, tz, live_rx, awc)
    gp = gaps(cfg, S, dense, truth, tz)
    lat = latency(dict(cfg, lag_model=cfg.get("lag_model")), S, awc, live if city != "Toronto" else (live[live.index.minute == 0] if live is not None else None), tz)
    v, why = verdict(meas, fl, nc, lat)
    res = dict(city=city, station=icao, source=cfg["source"], tz=tz, dense_identity=ident, metar=minfo,
               truth=dict(high=len(truth["high"]), low=len(truth["low"]),
                          eras={sd: dict(Counter(e for _, e in truth[sd].values())) for sd in ("high", "low")}),
               dense_range=[str(dense.index.min()), str(dense.index.max()), int(len(dense))],
               measurement=meas, floor=fl, nowcast=nc, gap_decomposition=gp, latency=lat,
               dense_lag_used_for_modeled_leads_min=r3(lag_model), verdict=v, verdict_reason=why, **extras.get(city, {}))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{city.lower()}.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"{city:10s} n={meas['n_pairs']} b={meas['gauss']['b']} s={meas['gauss']['sigma']} "
          f"m_high={fl['high'].get('m_c')} m_low={fl['low'].get('m_c')} -> {v}", flush=True)
    return res


def summary(results):
    def fmt_q(d):
        return "n/a" if not d or not d.get("n") else f"{d['p50']:+.0f} (p10 {d['p10']:+.0f}, n={d['n']})"
    rows = ["| city | station/source | n_pairs | b | σ | m_high / m_low | floor fires (days) | floor lead vs AWC (min) | nowcast Brier vs naive | gap (a)/(b)/(c)/(d) | verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m, fl, nc, gp = r["measurement"], r["floor"], r["nowcast"], r["gap_decomposition"]
        fh, fw = fl["high"], fl["low"]
        fires = f"H {fh['fires_m_c']['days_floor_reaches_settled']}/{fh['fires_m_c']['days']}; L {fw['fires_m_c']['days_floor_reaches_settled']}/{fw['fires_m_c']['days']}" if fh.get("n_days") and fw.get("n_days") else "n/a"

        def lead(f):
            fr = f.get("fires_m_c", {})
            lv = fr.get("lead_vs_awc_receipt_live_min", {})
            if lv.get("n", 0) >= 5:  # same rule as verdict(): live only with n >= 5
                return "live " + fmt_q(lv)
            return "model " + fmt_q(fr.get("lead_vs_awc_receipt_modeled_min", {}))
        leads = f"H {lead(fh)}; L {lead(fw)}" if fh.get("n_days") else "n/a"
        ncs = []
        for D, x in nc.items():
            if "scores" in x:
                ncs.append(f"Δ{D}{'*' if x['t0_in_settlement_set'] else ''}: {x['scores']['icm_dense_trend']['brier']:.3f} vs {x['scores']['naive_prev_metar']['brier']:.3f}")
        g = lambda sd: "/".join(str(gp[sd]["nonzero_days"][k]) for k in ("a_cadence", "b_measurement", "c_coverage", "d_era")) + f" of {gp[sd]['days']}"  # noqa: E731
        rows.append(f"| {r['city']} | {r['station']} / {r['source']} | {m['n_pairs']} | {m['gauss']['b']:+.3f} | {m['gauss']['sigma']:.3f} | "
                    f"{fh.get('m_c')} / {fw.get('m_c')} | {fires} | {leads} | {'; '.join(ncs) or 'n/a'} | H {g('high')}; L {g('low')} | {r['verdict']} |")
    return "\n".join(rows)


def main():
    nea_p50, nea_info = nea_lag()
    k_p50, k_info = knmi_lag()
    for c in CITIES:
        if c["city"] == "Singapore":
            c["lag_model"], c["lag_model_source"] = nea_p50, f"data.gov.sg v1 latest-endpoint probe: {nea_info}"
        if c["city"] == "Amsterdam":
            c["lag_model"], c["pub_lag"] = round(5 + k_p50, 2), k_p50
            c["lag_model_source"] = f"KNMI file publication lag p50 {k_p50} min after the :30/:00 stamp (+5 min to the bracketing stamp): {k_info}"
    nea = read_series(DAE / "raw/nea_s24_wsss.csv.gz")
    extras = {"Amsterdam": dict(interpolation_error=dict(
        nea_1min_10min_bracket_midpoint=interpolation_error(nea),
        note="sigma_total^2 = sigma_meas^2 + sigma_interp^2; sigma_interp from NEA 1-min (no sub-10-min KNMI ta at :25)"))}
    results = [run(c, extras) for c in CITIES]
    for r in results:
        if r["city"] == "Amsterdam":
            st, si = r["measurement"]["gauss"]["sigma"], extras["Amsterdam"]["interpolation_error"]["nea_1min_10min_bracket_midpoint"]["sd"]
            r["interpolation_error"]["sigma_meas_implied"] = r3(math.sqrt(max(st * st - si * si, 0)))
            (OUT / "amsterdam.json").write_text(json.dumps(r, indent=1, default=str))
    tbl = summary(results)
    (HERE / "summary_table.md").write_text(tbl + "\n")
    (HERE / "summary.json").write_text(json.dumps({r["city"]: dict(verdict=r["verdict"], reason=r["verdict_reason"]) for r in results}, indent=1))
    print(tbl)


if __name__ == "__main__":
    main()
