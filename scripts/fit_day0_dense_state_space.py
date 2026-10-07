#!/usr/bin/env python3
# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: artifacts/fast_obs_audit/dense_station_model/METHOD.md §5.3 (walk-forward
#   estimation, 70/30 inner selection); docs/operations/current/plans/
#   task_2026-10-07_dense_obs_probability_model.md (Implementation design).
"""Fit ``config/day0_dense_state_space_params.json`` walk-forward, with no look-ahead.

INPUTS (read-only)
  - Dense archives and METAR history checked in under artifacts/fast_obs_audit/
    (FMI 100968, JMA 44166, NEA S24, ECCC 51459 hourly; IEM METAR body TT groups).
  - Forecast path: Open-Meteo previous-runs ecmwf_ifs temperature_2m_previous_day1, a causal
    fixed-lead proxy, from the same archive.
  - Settlement labels: FORECAST settlement_outcomes (VERIFIED), used only to score the inner
    walk-forward model choice.
  - Page retention: WORLD observation_prints (AWC METAR instants vs noaa_wrh_<icao> rows).
Every DB is opened ``file:...?mode=ro`` with query_only.

WALK-FORWARD.  Training days are local days on or before ``--train-last``; page-retention evidence
ends at ``--retention-last``.  The served artifact applies only to target dates after both (loader
rule).  Model order ({one, two scales} x {plain,
shrunk mean}) is chosen by the inner split (first 70 % fit, last 30 % scored) with the shipped
operator, METAR-only and METAR + dense averaged, so the choice favours neither arm.  Parameter
uncertainty: ``--variants`` day-block bootstrap re-estimates of the chosen order.

OUTPUT: one JSON artifact (atomic replace), content-hashed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.calibration import day0_dense_state_space_fit as fit  # noqa: E402
from src.calibration.day0_dense_state_space_params import ARTIFACT_KIND, ARTIFACT_PATH, SCHEMA_VERSION, canonical_hash  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data import day0_dense_state_space as ds  # noqa: E402

AUDIT = REPO / "artifacts" / "fast_obs_audit"
DSM = AUDIT / "dense_station_model" / "raw"
DAE = AUDIT / "daily_extreme_agreement" / "raw"
STATE = Path(os.environ.get("ZEUS_STATE_DIR", "/Users/leofitz/zeus/state"))
COVER = 0.8
SEL_DECISION_STEP = 60.0
SEM = SettlementSemantics(resolution_source="fit", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")

CITIES = {
    "Helsinki": dict(icao="EFHK", tz="Europe/Helsinki", archive=DAE / "fmi_efhk.csv.gz", step=10,
                     dense_channel="fmi_airport_temperature", dense_max_age_minutes=25.0, routes=(),
                     speci_policy="none: Finland AIP GEN 3.5 (June 2026) issues no SPECI"),
    "Tokyo": dict(icao="RJTT", tz="Asia/Tokyo", archive=DSM / "jma_haneda_10min.csv.gz", step=10,
                  dense_channel="jma_amedas_temperature", dense_max_age_minutes=25.0,
                  routes=("jma_amedas_temperature",), speci_policy="measured"),
    "Singapore": dict(icao="WSSS", tz="Asia/Singapore", archive=DAE / "nea_s24_wsss.csv.gz", step=1,
                      dense_channel="nea_sg_air_temperature", dense_max_age_minutes=15.0, routes=(),
                      speci_policy="measured"),
    "Toronto": dict(icao="CYYZ", tz="America/Toronto", archive=None, step=60, dense_channel=None,
                    dense_max_age_minutes=0.0, routes=("eccc_swob_temperature",), speci_policy="measured"),
}


def R(v):
    return SEM.round_values(np.asarray(v, float)).astype(int)


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------------------- data

def _utc(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_series(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = gzip.open(path, "rt").read().splitlines()[1:]
    t, v = [], []
    for line in rows:
        a, b = line.split(",")[:2]
        if b:
            t.append(_utc(a).timestamp())
            v.append(float(b))
    t, v = np.asarray(t), np.asarray(v)
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    keep = np.r_[t[1:] != t[:-1], True]
    return t[keep], v[keep]


def load_metar(icao: str):
    """(epoch seconds, integer, routine) from IEM body TT groups; routine = dominant cadence minutes."""
    import re
    tt = re.compile(r"(?<=\s)(M?\d{2})/(M?\d{2}|//)?(?=\s|$)")
    rows = []
    for line in gzip.open(DSM / f"metar_iem_{icao}.csv.gz", "rt").read().splitlines()[1:]:
        _, valid, body = line.split(",", 2)
        m = tt.search(" " + body + " ")
        if m:
            g = m.group(1)
            rows.append((_utc(valid).timestamp(), -int(g[1:]) if g.startswith("M") else int(g)))
    rows = sorted(dict(rows).items())
    t = np.asarray([r[0] for r in rows])
    k = np.asarray([r[1] for r in rows], int)
    minutes = (t // 60 % 60).astype(int)
    common = Counter(minutes).most_common(2)
    n = 2 if len(common) == 2 and common[1][1] > 0.4 * common[0][1] else 1
    routine_minutes = sorted(int(m) for m, _ in common[:n])
    return t, k, np.isin(minutes, routine_minutes), routine_minutes


def load_forecast(city: str):
    d = json.loads(gzip.decompress((DSM / "ecmwf_ifs_previous_day1.json.gz").read_bytes()))["cities"][city]
    t = np.asarray([_utc(x).timestamp() for x in d["time"]])
    v = np.asarray([np.nan if x is None else float(x) for x in d["temp"]])
    return t, v


def load_truth(city: str) -> dict[str, dict[str, int]]:
    conn = ro(STATE / "zeus-forecasts.db")
    rows = conn.execute("SELECT target_date, temperature_metric, settlement_value, "
                        "json_extract(provenance_json, '$.data_version') FROM settlement_outcomes "
                        "WHERE city = ? AND authority = 'VERIFIED' AND settlement_value IS NOT NULL", (city,)).fetchall()
    conn.close()
    out: dict[str, dict[str, int]] = {"high": {}, "low": {}}
    for d, m, v, dv in rows:
        if dv == "noaa_wrh_timeseries_v1":
            out[m][d] = int(R(float(v)))
    return out


def page_retention(city: str, icao: str, train_last: str) -> dict:
    """Jeffreys mean of P(page keeps the AWC METAR integer at its instant), NOAA era, page days <= train_last."""
    from src.data.day0_fast_obs import metar_observation_time_from_raw

    conn = ro(STATE / "zeus-world.db")
    tz = ZoneInfo(CITIES[city]["tz"])
    page = {}
    for p, v in conn.execute("SELECT publish_ts_utc, value_native FROM observation_prints WHERE city = ? AND "
                             "source_channel = ?", (city, f"noaa_wrh_{icao.lower()}")):
        page[_utc(p).replace(second=0, microsecond=0)] = int(R(float(v)))
    awc = {}
    for raw, v, p in conn.execute("SELECT raw_report, value_native, publish_ts_utc FROM observation_prints WHERE "
                                  "city = ? AND source_channel = 'aviationweather_metar'", (city,)):
        t = metar_observation_time_from_raw(str(raw or ""), published_at=_utc(p))
        if t is not None:
            awc.setdefault(t.replace(second=0, microsecond=0), int(round(float(v))))
    conn.close()
    days = {t.astimezone(tz).date() for t in page}
    last_page = max(page) if page else None
    n = kept = 0
    misses = []
    for t, k in sorted(awc.items()):
        d = t.astimezone(tz).date()
        if d not in days or d.isoformat() > train_last or t > last_page:
            continue
        n += 1
        if page.get(t) == k:
            kept += 1
        else:
            misses.append([t.isoformat(), k, page.get(t)])
    return dict(s=(kept + 0.5) / (n + 1), kept=kept, n=n, first_misses=misses[:12],
                basis="AWC METAR instant vs noaa_wrh page row at the same instant, page-covered local days")


def local_bounds(d: date, tz: str) -> tuple[float, float]:
    z = ZoneInfo(tz)
    a = datetime.combine(d, datetime.min.time(), tzinfo=z).timestamp()
    b = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=z).timestamp()
    return a, b


def assemble(city: str):
    cfg = CITIES[city]
    tz = cfg["tz"]
    mt, mk, mroutine, routine_minutes = load_metar(cfg["icao"])
    ft, fv = load_forecast(city)
    if cfg["archive"] is not None:
        dt_, dv = load_series(cfg["archive"])
        if cfg["step"] == 1:
            sel = (dt_ // 60 % 5) == 0
            dt_, dv = dt_[sel], dv[sel]
    else:
        dt_, dv = np.zeros(0), np.zeros(0)
    step = 5 if cfg["step"] == 1 else cfg["step"]
    first = datetime.fromtimestamp(max(mt.min(), dt_.min() if dt_.size else mt.min()), tz=timezone.utc).astimezone(ZoneInfo(tz)).date() + timedelta(days=1)
    last = datetime.fromtimestamp(mt.max(), tz=timezone.utc).astimezone(ZoneInfo(tz)).date() - timedelta(days=1)
    days = []
    d = first
    while d <= last:
        a, b = local_bounds(d, tz)
        D = (b - a) / 60.0
        g = ds.grid_minutes(D)
        gsec = a + g * 60.0
        f = np.interp(gsec, ft, fv, left=np.nan, right=np.nan)
        window = (ft >= gsec[0] - 3600) & (ft <= gsec[-1] + 3600)
        ok_f = np.isfinite(f).all() and np.isfinite(fv[window]).all()
        hour = np.asarray([datetime.fromtimestamp(s, tz=timezone.utc).astimezone(ZoneInfo(tz)).hour for s in gsec])
        msel = (mt >= gsec[0]) & (mt < b)
        sched = [t for t in np.arange(0, D, 1.0) if int(((a + t * 60) // 60) % 60) in routine_minutes]
        got = int(((mt >= a) & (mt < b) & mroutine).sum())
        dsel = (dt_ >= gsec[0]) & (dt_ <= b)
        n_dense_day = int(((dt_ >= a) & (dt_ < b)).sum())
        dense_t = (dt_[dsel] - a) / 60.0
        days.append(dict(
            ok=ok_f and got >= COVER * max(len(sched), 1) and (cfg["archive"] is None or n_dense_day >= COVER * D / step),
            day=fit.TrainingDay(
                date=d.isoformat(), day_minutes=D, forecast=f, hour=hour,
                metar_t=(mt[msel] - a) / 60.0, metar_k=mk[msel], metar_routine=mroutine[msel],
                dense_t=dense_t, dense_x=dv[dsel],
                dense_hour=np.asarray([datetime.fromtimestamp(s, tz=timezone.utc).astimezone(ZoneInfo(tz)).hour
                                       for s in dt_[dsel]], int)),
            schedule=sched, local6=(datetime.combine(d, datetime.min.time(), tzinfo=ZoneInfo(tz)) + timedelta(hours=6)).timestamp(),
            start=a))
        d += timedelta(days=1)
    meta = dict(routine_minutes=routine_minutes, step=step,
                n_speci=int((~mroutine).sum()), n_metar=int(mt.size),
                metar_hours=float((mt.max() - mt.min()) / 3600.0))
    return [r for r in days if r["ok"]], meta


# ------------------------------------------------------------------------------- operator models

def operator_model(est: dict, order: str, speci_rate: float, with_noise: bool) -> ds.DenseModel:
    scales = order.split("|")[0]
    lat = fit.latent_for_operator(est["ou_one"] if scales == "one" else est["ou_two"])
    nz = est["noise"]
    return ds.DenseModel(
        ds.DenseLatent(lat["tau"], lat["s2"], lat["s2_static"]),
        ds.DenseNoise(tuple(nz["b_hour"]), nz["s1"], nz["s2"], nz["pi"], 0.1, nz["tau_e"], nz["sd2"]) if with_noise else None,
        ds.DenseMean(tuple(est["mean"]["mu_hour"]), est["mean"]["beta"]),
        speci_rate)


def model_block(est: dict, order: str, with_noise: bool) -> dict:
    scales = order.split("|")[0]
    lat = fit.latent_for_operator(est["ou_one"] if scales == "one" else est["ou_two"])
    return dict(latent=lat, mean=dict(mu_hour=est["mean"]["mu_hour"], beta=est["mean"]["beta"]),
                noise=dict(est["noise"], quantum=0.1) if with_noise else None)


def decision_day(rec: dict, t0: float, metric: str, with_dense: bool) -> ds.DenseDay:
    """The day as known at t0 (minutes): in-day METAR rows observed <= t0 as page rows (the archived
    tape), pre-midnight METARs as context, dense rows <= t0, pending = schedule after the last row."""
    d: fit.TrainingDay = rec["day"]
    msel = d.metar_t <= t0
    page = [(float(t), int(k)) for t, k in zip(d.metar_t[msel], d.metar_k[msel]) if 0 <= t < d.day_minutes]
    pre = [(float(t), int(k)) for t, k in zip(d.metar_t[msel], d.metar_k[msel]) if t < 0]
    dense = [(float(t), float(x)) for t, x in zip(d.dense_t, d.dense_x) if t <= t0] if with_dense else []
    return ds.build_day(metric=metric, day_minutes=d.day_minutes, forecast=d.forecast, hour=d.hour,
                        page=page, provisional=(), dense=dense, schedule=rec["schedule"], speci_from=t0,
                        context=pre)


def _score_job(args):
    model, rec, metric, truth, step, with_dense, cell = args
    out = []
    d: fit.TrainingDay = rec["day"]
    t = (rec["local6"] - rec["start"]) / 60.0
    while t < d.day_minutes:
        day = decision_day(rec, t, metric, with_dense)
        lo, hi = truth - 6, truth + 6
        bins = [(None, float(lo - 1))] + [(float(k), float(k)) for k in range(lo, hi + 1)] + [(float(hi + 1), None)]
        try:
            q = ds.bin_probabilities(model, day, bins, cell=cell)
            p = float(q[[i for i, b in enumerate(bins) if b == (float(truth), float(truth))][0]])
        except ValueError:
            p = 0.0
        out.append(-math.log(max(p, 1e-4)))
        t += step
    return out


def mean_logloss(model_a, model_b, recs, truth, workers, cell=0.1):
    jobs = []
    for rec in recs:
        for metric in ("high", "low"):
            k = truth[metric].get(rec["day"].date)
            if k is None:
                continue
            jobs.append((model_a, rec, metric, k, SEL_DECISION_STEP, False, cell))
            if model_b is not None:
                jobs.append((model_b, rec, metric, k, SEL_DECISION_STEP, True, cell))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        scores = [s for res in ex.map(_score_job, jobs, chunksize=1) for s in res]
    return float(np.mean(scores)) if scores else float("nan"), len(scores)


# ------------------------------------------------------------------------------- driver

def fit_city(city: str, train_last: str, retention_last: str, variants: int, workers: int, select: bool) -> dict:
    cfg = CITIES[city]
    t_start = time.time()
    recs, meta = assemble(city)
    train = [r for r in recs if r["day"].date <= train_last]
    if len(train) < 30:
        raise ValueError(f"{city}: only {len(train)} usable training days")
    cad = 30.0 if len(meta["routine_minutes"]) == 2 else 60.0
    with_noise = cfg["archive"] is not None
    speci_rate = 0.0 if cfg["speci_policy"].startswith("none") else meta["n_speci"] / (meta["metar_hours"] * 60.0)
    truth = load_truth(city)
    days = [r["day"] for r in train]
    selection = {}
    if select:
        n = len(train)
        inner_fit, inner_val = train[: int(0.7 * n)], train[int(0.7 * n):]
        for shrink in (False, True):
            est = fit.estimate([r["day"] for r in inner_fit], step=meta["step"], cadence=cad, shrink=shrink)
            for scales in ("one", "two"):
                order = f"{scales}|{'shrunk' if shrink else 'plain'}"
                try:
                    ma = operator_model(est, order, speci_rate, False)
                    mb = operator_model(est, order, speci_rate, True) if with_noise else None
                except ValueError as exc:
                    selection[order] = dict(rejected=str(exc))
                    continue
                score, count = mean_logloss(ma, mb, inner_val, truth, workers)
                selection[order] = dict(mean_logloss=round(score, 4), n=count, val_days=len(inner_val))
        picked = min((k for k, v in selection.items() if "mean_logloss" in v), key=lambda k: selection[k]["mean_logloss"])
    else:
        picked = cfg.get("order", "one|shrunk")
    est = fit.estimate(days, step=meta["step"], cadence=cad, shrink=picked.endswith("shrunk"))
    blocks = []
    rng = np.random.default_rng(20261007)
    for _ in range(variants):
        sample = [days[i] for i in rng.integers(0, len(days), len(days))]
        try:
            v = fit.estimate(sample, step=meta["step"], cadence=cad, shrink=picked.endswith("shrunk"))
            blocks.append(model_block(v, picked, with_noise))
        except (ValueError, np.linalg.LinAlgError):
            continue
    retention = page_retention(city, cfg["icao"], retention_last)
    retention["last_local_date"] = retention_last
    sources = {"metar_iem": _sha256(DSM / f"metar_iem_{cfg['icao']}.csv.gz"),
               "forecast_previous_day1": _sha256(DSM / "ecmwf_ifs_previous_day1.json.gz")}
    if cfg["archive"] is not None:
        sources["dense_archive"] = _sha256(cfg["archive"])
    return dict(
        station=cfg["icao"], timezone=cfg["tz"], metrics=["high", "low"], routine_minutes=meta["routine_minutes"],
        speci_rate_per_min=speci_rate, speci_policy=cfg["speci_policy"], page_retention=retention,
        dense_channel=cfg["dense_channel"], dense_max_age_minutes=cfg["dense_max_age_minutes"],
        provisional_route_channels=list(cfg["routes"]),
        model=model_block(est, picked, with_noise), variants=blocks,
        training=dict(first=days[0].date, last=days[-1].date, n_days=len(days)),
        selection=dict(picked=picked, inner_walk_forward=selection),
        diagnostics=dict(noise_choice=est["noise_choice"], n_pairs=est["n_pairs"], ou_one=est["ou_one"],
                         ou_two=est["ou_two"], drift_lags=est["drift_lags"], drift_rhos=est["drift_rhos"]),
        sources=sources, runtime_s=round(time.time() - t_start, 1))


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cities", nargs="*", default=list(CITIES))
    ap.add_argument("--train-last", required=True, help="last training local date, YYYY-MM-DD")
    ap.add_argument("--retention-last", required=True,
                    help="last local date of page-retention evidence (live WORLD rows), YYYY-MM-DD")
    ap.add_argument("--variants", type=int, default=8)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-select", action="store_true", help="skip inner walk-forward model choice (one|shrunk)")
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    args = ap.parse_args()
    cities = {}
    for city in args.cities:
        cities[city] = fit_city(city, args.train_last, args.retention_last, args.variants, args.workers,
                                not args.no_select)
        print(city, cities[city]["selection"]["picked"], cities[city]["page_retention"]["s"],
              cities[city]["runtime_s"], flush=True)
    payload = dict(schema_version=SCHEMA_VERSION, artifact=ARTIFACT_KIND, data_version="day0_dense_state_space_v1",
                   training_cutoff=args.train_last, retention_cutoff=args.retention_last, fitted_at=datetime.now(timezone.utc).isoformat(),
                   cities=cities)
    payload = json.loads(json.dumps(payload, default=_jsonable))
    payload["content_hash"] = canonical_hash(payload)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    tmp.replace(args.out)
    print("wrote", args.out, payload["content_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
