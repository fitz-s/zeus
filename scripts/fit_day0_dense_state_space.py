#!/usr/bin/env python3
# Created: 2026-10-07
# Last reused or audited: 2026-10-08
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (D2 lifecycle, D6 walk-forward requalification); artifacts/fast_obs_audit/dense_station_model/METHOD.md §5.
"""Fit and qualify ``config/day0_dense_state_space_params.json`` walk-forward (schema 2).

INPUTS (read-only; every DB opened ``file:...?mode=ro`` with query_only)
  - Dense archives and METAR history under artifacts/fast_obs_audit/ (FMI 100968, JMA 44166, NEA S24,
    ECCC 51459; IEM METAR bodies).
  - Forecast path: Open-Meteo previous-runs ecmwf_ifs temperature_2m_previous_day1, a causal fixed-lead proxy.
  - Labels: FORECAST settlement_outcomes (VERIFIED, NOAA era), for the inner model choice and qualification.
  - Report lifecycle and page visibility: WORLD observation_prints (AWC reports vs noaa_wrh page rows).

WALK-FORWARD.  Every fit input is cut at ``--train-last`` before the fit runs: dense and METAR rows,
the forecast path, the routine-minute schedule, the SPECI rate, labels and the lifecycle cohort.
Model order is chosen by the inner split (first 70 % fit, last 30 % scored) inside the training days.

QUALIFICATION (D6).  Held-out local days after ``--train-last`` (through ``--test-last``), decisions
every ``--step`` minutes from local 06:00, scored on the settled value with the production SELECT
assembly (src.data.day0_dense_evidence.assemble_sealed) on rows replayed from the archive:
  A0  the live legacy posterior: the newest forecast_posteriors q computed by the decision (receipt-
      frozen; skipped where none exists);
  A   the corrected law without dense evidence;
  B   the corrected law with dense evidence.
Reports true log score (no floor), Brier, day-block bootstrap of B - A and B - A0, calibration of the
top bin, high-confidence (q >= 0.95) error counts with denominators against the model-expected
Poisson bound, and semantic violations.  A metric is eligible iff B - A0 and B - A log-score 95 %
day-block upper bounds are < 0, zero semantic violations, and B's high-confidence errors lie within
the 95 % Poisson bound of its own expected count.  ``metrics`` per city is that verdict, and the
qualification report's hash is bound into the artifact (``qualification_hash``).

OUTPUT: the artifact (atomic replace, unique temp file) and ``--report`` (qualification JSON).
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
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.calibration import day0_dense_state_space_fit as fit  # noqa: E402
from src.calibration.day0_dense_state_space_params import (  # noqa: E402
    ARTIFACT_KIND, ARTIFACT_PATH, SCHEMA_VERSION, block_hash, canonical_hash, _city as parse_city_block,
)
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data import day0_dense_state_space as ds  # noqa: E402

AUDIT = REPO / "artifacts" / "fast_obs_audit"
DSM = AUDIT / "dense_station_model" / "raw"
DAE = AUDIT / "daily_extreme_agreement" / "raw"
STATE = Path(os.environ.get("ZEUS_STATE_DIR", "/Users/leofitz/zeus/state"))
COVER = 0.8
SEL_DECISION_STEP = 60.0
UTC = timezone.utc
SEM = SettlementSemantics(resolution_source="fit", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")

CITIES = {
    "Helsinki": dict(icao="EFHK", tz="Europe/Helsinki", archive=DAE / "fmi_efhk.csv.gz", step=10,
                     dense_channel="fmi_airport_temperature", dense_max_age_minutes=25.0,
                     speci_policy="none: Finland AIP GEN 3.5 (June 2026) issues no SPECI"),
    "Tokyo": dict(icao="RJTT", tz="Asia/Tokyo", archive=DSM / "jma_haneda_10min.csv.gz", step=10,
                  dense_channel="jma_amedas_temperature", dense_max_age_minutes=25.0, speci_policy="measured"),
    "Singapore": dict(icao="WSSS", tz="Asia/Singapore", archive=DAE / "nea_s24_wsss.csv.gz", step=1,
                      dense_channel="nea_sg_air_temperature", dense_max_age_minutes=15.0, speci_policy="measured"),
}


def R(v):
    return SEM.round_values(np.asarray(v, float)).astype(int)


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(text: str) -> datetime:
    d = datetime.fromisoformat(str(text).replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def local_bounds(d: date, tz: str) -> tuple[float, float]:
    z = ZoneInfo(tz)
    a = datetime.combine(d, datetime.min.time(), tzinfo=z).timestamp()
    b = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=z).timestamp()
    return a, b


def day_end_epoch(d: str, tz: str) -> float:
    return local_bounds(date.fromisoformat(d), tz)[1]


# ------------------------------------------------------------------------------- archives (cut before use)

def load_series(path: Path, until: float) -> tuple[np.ndarray, np.ndarray]:
    rows = gzip.open(path, "rt").read().splitlines()[1:]
    t, v = [], []
    for line in rows:
        a, b = line.split(",")[:2]
        if b:
            s = _utc(a).timestamp()
            if s < until:
                t.append(s)
                v.append(float(b))
    t, v = np.asarray(t), np.asarray(v)
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    keep = np.r_[t[1:] != t[:-1], True] if t.size else np.zeros(0, bool)
    return t[keep], v[keep]


def load_metar(icao: str, until: float):
    """(epoch seconds, integer, speci) from IEM body TT groups, rows before ``until`` only."""
    import re
    tt = re.compile(r"(?<=\s)(M?\d{2})/(M?\d{2}|//)?(?=\s|$)")
    rows = {}
    for line in gzip.open(DSM / f"metar_iem_{icao}.csv.gz", "rt").read().splitlines()[1:]:
        _, valid, body = line.split(",", 2)
        s = _utc(valid).timestamp()
        if s >= until:
            continue
        m = tt.search(" " + body + " ")
        if m:
            g = m.group(1)
            rows[s] = (-int(g[1:]) if g.startswith("M") else int(g), "SPECI" in body.upper())
    t = np.asarray(sorted(rows))
    return t, np.asarray([rows[s][0] for s in t], int), np.asarray([rows[s][1] for s in t], bool)


def routine_minutes_of(t: np.ndarray, speci: np.ndarray) -> list[int]:
    minutes = (t[~speci] // 60 % 60).astype(int)
    common = Counter(minutes).most_common(2)
    n = 2 if len(common) == 2 and common[1][1] > 0.4 * common[0][1] else 1
    return sorted(int(m) for m, _ in common[:n])


def load_forecast(city: str, until: float):
    d = json.loads(gzip.decompress((DSM / "ecmwf_ifs_previous_day1.json.gz").read_bytes()))["cities"][city]
    t = np.asarray([_utc(x).timestamp() for x in d["time"]])
    v = np.asarray([np.nan if x is None else float(x) for x in d["temp"]])
    keep = t < until
    return t[keep], v[keep]


def load_truth(city: str, last: str) -> dict[str, dict[str, int]]:
    conn = ro(STATE / "zeus-forecasts.db")
    rows = conn.execute("SELECT target_date, temperature_metric, settlement_value, "
                        "json_extract(provenance_json, '$.data_version') FROM settlement_outcomes "
                        "WHERE city = ? AND authority = 'VERIFIED' AND settlement_value IS NOT NULL AND target_date <= ?",
                        (city, last)).fetchall()
    conn.close()
    out: dict[str, dict[str, int]] = {"high": {}, "low": {}}
    for d, m, v, dv in rows:
        if dv == "noaa_wrh_timeseries_v1":
            out[m][d] = int(R(float(v)))
    return out


# ------------------------------------------------------------------------------- lifecycle (D2), pooled over C stations

def lifecycle_inputs(last: str) -> tuple[list[dict], list[tuple[float, bool]]]:
    """Reports and fetch checks for every C NOAA station, page days finalized by ``last``."""
    from src.config import runtime_cities_by_name
    from src.data.day0_fast_obs import metar_observation_time_from_raw

    conn = ro(STATE / "zeus-world.db")
    cutoff = datetime.combine(date.fromisoformat(last) + timedelta(days=2), datetime.min.time(), tzinfo=UTC)
    reports, checks = [], []
    for name, city in sorted(runtime_cities_by_name().items()):
        icao = str(getattr(city, "wu_station", "") or "").upper()
        if str(getattr(city, "settlement_source_type", "")).lower() != "noaa" or city.settlement_unit != "C":
            continue
        tz = ZoneInfo(city.timezone)
        final, first_seen, intraday_fetches = {}, {}, []
        for p, v, f, raw in conn.execute(
                "SELECT publish_ts_utc, value_native, fetched_at_utc, raw_report FROM observation_prints "
                "WHERE city = ? AND source_channel = ? AND fetched_at_utc < ? ORDER BY fetched_at_utc, id",
                (name, f"noaa_wrh_{icao.lower()}", cutoff.isoformat())):
            t, rec = _utc(p).replace(second=0, microsecond=0), _utc(f)
            final[t] = int(R(float(v)))
            first_seen.setdefault(t, rec)
            if str(raw or "").lstrip().startswith("{"):
                intraday_fetches.append(rec)
        if not final:
            continue
        days = defaultdict(list)
        for t in final:
            days[t.astimezone(tz).date()].append(t)
        from src.data.day0_dense_evidence import PAGE_FETCH_COVER_MINUTES

        for rec in sorted(set(intraday_fetches)):
            for t, seen in first_seen.items():
                if rec - timedelta(minutes=PAGE_FETCH_COVER_MINUTES) <= t <= rec:
                    checks.append(((rec - t).total_seconds() / 60.0, seen <= rec))
        awc = {}
        for raw, v, p in conn.execute("SELECT raw_report, value_native, publish_ts_utc FROM observation_prints "
                                      "WHERE city = ? AND source_channel = 'aviationweather_metar'", (name,)):
            obs = metar_observation_time_from_raw(str(raw or ""), published_at=_utc(p))
            if obs is not None:
                obs = obs.replace(second=0, microsecond=0)
                awc.setdefault(obs, (int(R(float(v))), "SPECI" in str(raw or "")[:12]))
        for t, (k, speci) in awc.items():
            d = t.astimezone(tz).date()
            if d not in days or d.isoformat() > last or (max(days[d]) - min(days[d])) < timedelta(hours=20):
                continue
            pk = final.get(t)
            gap = None
            if pk is None:
                near = [v for tt, v in final.items() if abs((tt - t).total_seconds()) <= 3600]
                gap = min(abs(k - v) for v in near) if near else None
            reports.append(dict(station=icao, utc_hour=t.replace(minute=0).isoformat(), speci=speci,
                                outcome="kept" if pk == k else ("corrected" if pk is not None else "absent"),
                                delta=None if pk is None else pk - k, neighbour_gap=gap))
    conn.close()
    return reports, checks


# ------------------------------------------------------------------------------- days

def assemble(city: str, until: float, routine_minutes=None):
    cfg = CITIES[city]
    tz = cfg["tz"]
    mt, mk, mspeci = load_metar(cfg["icao"], until)
    if routine_minutes is None:
        routine_minutes = routine_minutes_of(mt, mspeci)
    mroutine = np.isin((mt // 60 % 60).astype(int), routine_minutes) & ~mspeci
    ft, fv = load_forecast(city, until)
    dt_, dv = load_series(cfg["archive"], until)
    if cfg["step"] == 1:
        sel = (dt_ // 60 % 5) == 0
        dt_, dv = dt_[sel], dv[sel]
    step = 5 if cfg["step"] == 1 else cfg["step"]
    z = ZoneInfo(tz)
    first = datetime.fromtimestamp(max(mt.min(), dt_.min()), tz=UTC).astimezone(z).date() + timedelta(days=1)
    last = datetime.fromtimestamp(min(mt.max(), dt_.max()), tz=UTC).astimezone(z).date() - timedelta(days=1)
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
        hour = np.asarray([datetime.fromtimestamp(s, tz=UTC).astimezone(z).hour for s in gsec])
        msel = (mt >= gsec[0]) & (mt < b)
        sched = [t for t in np.arange(0, D, 1.0) if int(((a + t * 60) // 60) % 60) in routine_minutes]
        got = int(((mt >= a) & (mt < b) & mroutine).sum())
        dsel = (dt_ >= gsec[0]) & (dt_ <= b)
        n_dense_day = int(((dt_ >= a) & (dt_ < b)).sum())
        days.append(dict(
            ok=bool(ok_f and got >= COVER * max(len(sched), 1) and n_dense_day >= COVER * D / step),
            day=fit.TrainingDay(
                date=d.isoformat(), day_minutes=D, forecast=f, hour=hour,
                metar_t=(mt[msel] - a) / 60.0, metar_k=mk[msel], metar_routine=mroutine[msel],
                dense_t=(dt_[dsel] - a) / 60.0, dense_x=dv[dsel],
                dense_hour=np.asarray([datetime.fromtimestamp(s, tz=UTC).astimezone(z).hour for s in dt_[dsel]], int)),
            schedule=sched, start=a,
            local6=(datetime.combine(d, datetime.min.time(), tzinfo=z) + timedelta(hours=6)).timestamp()))
        d += timedelta(days=1)
    speci_rate = float(mspeci.sum()) / max((mt.max() - mt.min()) / 60.0, 1.0)
    return [r for r in days if r["ok"]], dict(routine_minutes=routine_minutes, step=step, speci_rate=speci_rate)


# ------------------------------------------------------------------------------- operator models

def operator_model(est: dict, order: str, with_noise: bool) -> ds.DenseModel:
    lat = fit.latent_for_operator(est["ou_one"] if order.startswith("one") else est["ou_two"])
    nz = est["noise"]
    return ds.DenseModel(
        ds.DenseLatent(lat["tau"], lat["s2"], lat["s2_static"]),
        ds.DenseNoise(tuple(nz["b_hour"]), nz["s1"], nz["s2"], nz["pi"], 0.1, nz["tau_e"], nz["sd2"]) if with_noise else None,
        ds.DenseMean(tuple(est["mean"]["mu_hour"]), est["mean"]["beta"]))


def model_block(est: dict, order: str, with_noise: bool) -> dict:
    lat = fit.latent_for_operator(est["ou_one"] if order.startswith("one") else est["ou_two"])
    return dict(latent=lat, mean=dict(mu_hour=est["mean"]["mu_hour"], beta=est["mean"]["beta"]),
                noise=dict(est["noise"], quantum=0.1) if with_noise else None)


def archive_day(rec: dict, t0: float, metric: str, lc: dict, with_dense: bool, speci_rate: float) -> ds.DenseDay:
    """The day at cut t0 from the archived tape (complete through t0: page rows), the routine schedule
    after t0 pending with the lifecycle's kept/corrected weights."""
    d: fit.TrainingDay = rec["day"]
    sel = d.metar_t <= t0
    page = {}
    for t, k in zip(d.metar_t[sel], d.metar_k[sel]):
        if 0 <= t < d.day_minutes:
            page[float(t)] = int(k)
    context = tuple((float(t), int(k)) for t, k in zip(d.metar_t[sel], d.metar_k[sel]) if t < 0)
    pending = tuple((float(t), lc["kept"]["routine"], lc["corrected"]["routine"]) for t in rec["schedule"]
                    if t > t0 and float(t) not in page)
    dense = tuple((float(t), float(x)) for t, x in zip(d.dense_t, d.dense_x) if t <= t0) if with_dense else ()
    return ds.DenseDay(metric=metric, day_minutes=d.day_minutes, forecast=tuple(float(v) for v in d.forecast),
                       hour=tuple(int(h) for h in d.hour), page=tuple(sorted(page.items())), pending=pending,
                       context=context, dense=dense, delta=tuple((int(a), float(b)) for a, b in lc["delta"]),
                       speci_from=t0, speci_rate=speci_rate)


def _score_job(args):
    model, rec, metric, truth, step, with_dense, lc, speci_rate, cell = args
    out = []
    d: fit.TrainingDay = rec["day"]
    t = (rec["local6"] - rec["start"]) / 60.0
    while t < d.day_minutes:
        day = archive_day(rec, t, metric, lc, with_dense, speci_rate)
        lo, hi = truth - 6, truth + 6
        bins = [(None, float(lo - 1))] + [(float(k), float(k)) for k in range(lo, hi + 1)] + [(float(hi + 1), None)]
        q = ds.bin_probabilities(model, day, bins, cell=cell)
        out.append(dict(t=t, q=q.tolist(), truth_index=bins.index((float(truth), float(truth))),
                        allowed=list(ds.semantic_support(day, bins))))
        t += step
    return out


# ------------------------------------------------------------------------------- scoring

def score(decisions: list[dict]) -> dict:
    """Per decision: true log score, Brier, top-bin calibration, high-confidence errors, semantic violations."""
    out = []
    for dcs in decisions:
        q = np.asarray(dcs["q"], float)
        i = dcs["truth_index"]
        onehot = np.zeros_like(q)
        onehot[i] = 1.0
        top = int(np.argmax(q))
        out.append(dict(ll=-math.log(q[i]) if q[i] > 0 else math.inf, brier=float(((q - onehot) ** 2).sum()),
                        top_p=float(q[top]), top_hit=top == i, hc=q[top] >= 0.95, hc_err=q[top] >= 0.95 and top != i,
                        sem_violation=float(sum(p for p, ok in zip(q, dcs["allowed"]) if not ok)) > 0, date=dcs["date"]))
    return out


def day_block_ci(diffs_by_day: dict[str, list[float]], n_boot=4000, seed=7) -> dict:
    days = sorted(diffs_by_day)
    if not days:
        return dict(mean=None, lo=None, hi=None, days=0, decisions=0)
    arr = [np.asarray(diffs_by_day[d], float) for d in days]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(days), len(days))
        allv = np.concatenate([arr[p] for p in pick])
        means.append(float(np.mean(allv)))
    allv = np.concatenate(arr)
    return dict(mean=float(np.mean(allv)), lo=float(np.percentile(means, 2.5)), hi=float(np.percentile(means, 97.5)),
                days=len(days), decisions=int(allv.size))


def hc_summary(rows: list[dict]) -> dict:
    hc = [r for r in rows if r["hc"]]
    expected = float(sum(1 - r["top_p"] for r in hc))
    errors = int(sum(r["hc_err"] for r in hc))
    bound = int(stats.poisson.ppf(0.95, expected)) if expected > 0 else 0
    return dict(n=len(hc), decisions=len(rows), errors=errors, expected_errors=round(expected, 4), poisson95=bound,
                within=errors <= bound)


def calibration(rows: list[dict]) -> list[dict]:
    edges = [0.0, 0.5, 0.7, 0.9, 0.95, 0.99, 1.0001]
    out = []
    for a, b in zip(edges, edges[1:]):
        sel = [r for r in rows if a <= r["top_p"] < b]
        if sel:
            out.append(dict(bin=[a, min(b, 1.0)], n=len(sel), mean_p=round(float(np.mean([r["top_p"] for r in sel])), 4),
                            hit_rate=round(float(np.mean([r["top_hit"] for r in sel])), 4)))
    return out


def a0_posteriors(city: str, metric: str, dates: set[str]) -> dict[str, list]:
    """Live legacy posteriors per target date: (computed_at epoch, [(lo, hi, q)]) sorted, read-only."""
    conn = ro(STATE / "zeus-forecasts.db")
    out: dict[str, list] = defaultdict(list)
    for td, comp, qj, topo in conn.execute(
            "SELECT target_date, computed_at, q_json, json_extract(provenance_json, '$.bin_topology') FROM forecast_posteriors "
            "WHERE city = ? AND temperature_metric = ? AND runtime_layer = 'live' AND target_date >= ? "
            "AND julianday(computed_at) >= julianday(target_date) - 1",
            (city, metric, min(dates) if dates else "9999")):
        if td not in dates or not topo:
            continue
        try:
            q = json.loads(qj)
            bins = [(b["lower_c"], b["upper_c"], float(q[b["bin_id"]])) for b in json.loads(topo)]
        except (KeyError, TypeError, ValueError):
            continue
        out[td].append((_utc(comp).timestamp(), bins))
    conn.close()
    for v in out.values():
        v.sort(key=lambda x: x[0])
    return out


def _arm_stats(q: np.ndarray, truth_index: int, allowed=None) -> dict:
    p = float(q[truth_index])
    top = int(np.argmax(q))
    onehot = np.zeros_like(q)
    onehot[truth_index] = 1.0
    return dict(ll=-math.log(p) if p > 0 else math.inf, brier=float(((q - onehot) ** 2).sum()), top_p=float(q[top]),
                top_hit=top == truth_index, hc=q[top] >= 0.95, hc_err=bool(q[top] >= 0.95 and top != truth_index),
                sem_violation=bool(allowed is not None and sum(v for v, ok in zip(q, allowed) if not ok) > 0))


def _ledger_job(args):
    """Receipt-frozen decisions of one (city, date, metric) through the production SELECT assembly."""
    import bisect

    from src.data import day0_dense_evidence as ev

    city, block, date_text, metric, truth, a0_posts, step, cell, forecast_t, forecast_v = args
    params = parse_city_block(city, block)
    model_b = params.model
    model_a = ds.DenseModel(params.model.latent, None, params.model.mean)
    offline = ev.OfflineInputs(params=params, forecast=lambda start, minutes: np.interp(
        start.timestamp() + np.asarray(minutes) * 60.0, forecast_t, forecast_v))
    conn = ro(STATE / "zeus-world.db")
    tz = ZoneInfo(CITIES[city]["tz"])
    target = date.fromisoformat(date_text)
    start, end = local_bounds(target, CITIES[city]["tz"])
    t = (datetime.combine(target, datetime.min.time(), tzinfo=tz) + timedelta(hours=6)).timestamp()
    times = [p[0] for p in a0_posts]
    out = []
    while t < end:
        cut = datetime.fromtimestamp(t, tz=UTC)
        i = bisect.bisect_right(times, t) - 1
        row = dict(t=t, served=False, reason=None)
        if i < 0:
            row["reason"] = "A0_ABSENT"
            out.append(row)
            t += step * 60.0
            continue
        a0 = a0_posts[i][1]
        bins = [(None if lo is None else float(lo), None if hi is None else float(hi)) for lo, hi, _ in a0]
        idx = next((j for j, (lo, hi) in enumerate(bins) if (lo is None or truth >= lo) and (hi is None or truth <= hi)), None)
        if idx is None:
            row["reason"] = "TRUTH_OUTSIDE_TOPOLOGY"
            out.append(row)
            t += step * 60.0
            continue
        q0 = np.asarray([p for _, _, p in a0], float)
        q0 = q0 / q0.sum()
        row["A0"] = _arm_stats(q0, idx)
        prepared = ev.prepare_dense_request(conn, city=city, metric=metric, target=target, cut=cut, semantics=SEM,
                                            offline=offline)
        if not prepared.serves:
            row["reason"] = prepared.reason
            out.append(row)
            t += step * 60.0
            continue
        day_b = ev._day_from_sealed(prepared.sealed)
        day_a = ev._day_from_sealed({**prepared.sealed, "dense": []})
        allowed = ds.semantic_support(day_b, bins)
        try:
            row["B"] = _arm_stats(ds.bin_probabilities(model_b, day_b, bins, cell=cell), idx, allowed)
            row["A"] = _arm_stats(ds.bin_probabilities(model_a, day_a, bins, cell=cell), idx, allowed)
            row["served"] = True
        except ValueError as exc:
            row["reason"] = f"COMPUTE:{exc}"
        out.append(row)
        t += step * 60.0
    conn.close()
    return out


def hc_summary(rows: list[dict]) -> dict:
    hc = [r for r in rows if r["hc"]]
    expected = float(sum(1 - r["top_p"] for r in hc))
    errors = int(sum(r["hc_err"] for r in hc))
    bound = int(stats.poisson.ppf(0.95, expected)) if expected > 0 else 0
    return dict(n=len(hc), decisions=len(rows), errors=errors, expected_errors=round(expected, 4), poisson95=bound,
                within=errors <= bound)


def calibration(rows: list[dict]) -> list[dict]:
    edges = [0.0, 0.5, 0.7, 0.9, 0.95, 0.99, 1.0001]
    out = []
    for a, b in zip(edges, edges[1:]):
        sel = [r for r in rows if a <= r["top_p"] < b]
        if sel:
            out.append(dict(bin=[a, min(b, 1.0)], n=len(sel), mean_p=round(float(np.mean([r["top_p"] for r in sel])), 4),
                            hit_rate=round(float(np.mean([r["top_hit"] for r in sel])), 4)))
    return out


def day_block_ci(diffs_by_day: dict[str, list[float]], n_boot=4000, seed=7) -> dict:
    days = sorted(d for d, v in diffs_by_day.items() if v)
    if not days:
        return dict(mean=None, lo=None, hi=None, days=0, decisions=0)
    arr = [np.asarray(diffs_by_day[d], float) for d in days]
    rng = np.random.default_rng(seed)
    means = [float(np.mean(np.concatenate([arr[p] for p in rng.integers(0, len(days), len(days))])))
             for _ in range(n_boot)]
    allv = np.concatenate(arr)
    return dict(mean=float(np.mean(allv)), lo=float(np.percentile(means, 2.5)), hi=float(np.percentile(means, 97.5)),
                days=len(days), decisions=int(allv.size))


def qualify(city: str, block: dict, test_dates: list[str], metric: str, truth: dict, workers: int, step: float,
            cell: float, forecast: tuple) -> dict:
    """D6 on receipt-frozen held-out decisions: A0 (live legacy) vs A (corrected, no dense) vs B (corrected,
    dense), identical decisions and bins (A0's own topology), outages clustered by day."""
    dates = [d for d in test_dates if d in truth]
    posts = a0_posteriors(city, metric, set(dates))
    jobs = [(city, block, d, metric, truth[d], posts.get(d, []), step, cell, *forecast) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for d, res in zip(dates, ex.map(_ledger_job, jobs, chunksize=1)):
            rows += [dict(r, date=d) for r in res]
    served = [r for r in rows if r["served"]]
    reasons = Counter(r["reason"] for r in rows if not r["served"])
    diff = lambda a, b: {d: [r[b]["ll"] - r[a]["ll"] for r in served if r["date"] == d  # noqa: E731
                             and math.isfinite(r[b]["ll"]) and math.isfinite(r[a]["ll"])] for d in {r["date"] for r in served}}
    arms = {arm: [r[arm] for r in served] for arm in ("A0", "A", "B")}
    infinite = {arm: sum(not math.isfinite(x["ll"]) for x in v) for arm, v in arms.items()}
    rep = dict(
        n_days=len({r["date"] for r in served}), n_decisions=len(rows), n_served=len(served), not_served=dict(reasons),
        logscore={arm: (float(np.mean([x["ll"] for x in v if math.isfinite(x["ll"])])) if v else None) for arm, v in arms.items()},
        infinite_logscore=infinite,
        brier={arm: (float(np.mean([x["brier"] for x in v])) if v else None) for arm, v in arms.items()},
        B_minus_A=day_block_ci(diff("A", "B")), B_minus_A0=day_block_ci(diff("A0", "B")),
        A_minus_A0=day_block_ci(diff("A0", "A")),
        hc={arm: hc_summary(v) for arm, v in arms.items()},
        calibration={arm: calibration(v) for arm, v in arms.items()},
        semantic_violations={arm: sum(x["sem_violation"] for x in v) for arm, v in arms.items() if arm != "A0"},
    )
    rep["eligible"] = bool(
        rep["n_served"] > 0
        and rep["B_minus_A"]["hi"] is not None and rep["B_minus_A"]["hi"] < 0
        and rep["B_minus_A0"]["hi"] is not None and rep["B_minus_A0"]["hi"] < 0
        and rep["semantic_violations"]["B"] == 0 and rep["hc"]["B"]["within"] and infinite["B"] == 0)
    return rep


# ------------------------------------------------------------------------------- driver

def fit_city(city: str, train_last: str, test_last: str, lc: dict, lc_last: str, variants: int, workers: int,
             step: float, cell: float) -> tuple[dict, dict]:
    cfg = CITIES[city]
    t_start = time.time()
    cut = day_end_epoch(train_last, cfg["tz"])
    recs, meta = assemble(city, cut)
    train = [r for r in recs if r["day"].date <= train_last]
    if len(train) < 30:
        raise ValueError(f"{city}: only {len(train)} usable training days")
    cad = 30.0 if len(meta["routine_minutes"]) == 2 else 60.0
    speci_rate = 0.0 if cfg["speci_policy"].startswith("none") else meta["speci_rate"]
    meta["speci_rate"] = speci_rate
    truth = load_truth(city, train_last)
    days = [r["day"] for r in train]
    selection = {}
    n = len(train)
    inner_fit, inner_val = train[: int(0.7 * n)], train[int(0.7 * n):]
    for shrink in (False, True):
        est = fit.estimate([r["day"] for r in inner_fit], step=meta["step"], cadence=cad, shrink=shrink)
        for scales in ("one", "two"):
            order = f"{scales}|{'shrunk' if shrink else 'plain'}"
            try:
                ma, mb = operator_model(est, order, False), operator_model(est, order, True)
            except ValueError as exc:
                selection[order] = dict(rejected=str(exc))
                continue
            lls = []
            for metric in ("high", "low"):
                for arm_model, dense in ((ma, False), (mb, True)):
                    jobs = [(arm_model, rec, metric, truth[metric][rec["day"].date], SEL_DECISION_STEP, dense, lc,
                             speci_rate, 0.1) for rec in inner_val if rec["day"].date in truth[metric]]
                    with ProcessPoolExecutor(max_workers=workers) as ex:
                        for res in ex.map(_score_job, jobs, chunksize=1):
                            lls += [r["ll"] for r in score([dict(r, date="") for r in res])]
            finite = [v for v in lls if math.isfinite(v)]
            selection[order] = dict(mean_logscore=round(float(np.mean(finite)), 4) if finite else None,
                                    n=len(lls), infinite=len(lls) - len(finite), val_days=len(inner_val))
    picked = min((k for k, v in selection.items() if v.get("mean_logscore") is not None and v["infinite"] == 0),
                 key=lambda k: selection[k]["mean_logscore"])
    est = fit.estimate(days, step=meta["step"], cadence=cad, shrink=picked.endswith("shrunk"))
    blocks = []
    rng = np.random.default_rng(20261008)
    for _ in range(variants):
        sample = [days[i] for i in rng.integers(0, len(days), len(days))]
        try:
            v = fit.estimate(sample, step=meta["step"], cadence=cad, shrink=picked.endswith("shrunk"))
            blocks.append(model_block(v, picked, True))
        except (ValueError, np.linalg.LinAlgError):
            continue
    sources = {"metar_iem": _sha256(DSM / f"metar_iem_{cfg['icao']}.csv.gz"),
               "forecast_previous_day1": _sha256(DSM / "ecmwf_ifs_previous_day1.json.gz"),
               "dense_archive": _sha256(cfg["archive"])}
    lifecycle = {k: lc[k] for k in ("kept", "corrected", "removed", "gross", "outage_prior", "delta", "visibility")}
    lifecycle["last_local_date"] = lc_last
    block = dict(
        station=cfg["icao"], timezone=cfg["tz"], metrics=[], routine_minutes=meta["routine_minutes"],
        speci_rate_per_min=speci_rate, speci_policy=cfg["speci_policy"], lifecycle=lifecycle,
        dense_channel=cfg["dense_channel"], dense_max_age_minutes=cfg["dense_max_age_minutes"],
        model=model_block(est, picked, True), variants=blocks,
        training=dict(first=days[0].date, last=days[-1].date, n_days=len(days)),
        selection=dict(picked=picked, inner_walk_forward=selection),
        diagnostics=dict(noise_choice=est["noise_choice"], n_pairs=est["n_pairs"], ou_one=est["ou_one"],
                         ou_two=est["ou_two"], drift_lags=est["drift_lags"], drift_rhos=est["drift_rhos"]),
        sources=sources)
    # D6: receipt-frozen held-out decisions after the training cut, through test_last.
    first = date.fromisoformat(train_last) + timedelta(days=1)
    test_dates = [(first + timedelta(days=i)).isoformat()
                  for i in range((date.fromisoformat(test_last) - first).days + 1)]
    truth_all = load_truth(city, test_last)
    forecast = load_forecast(city, day_end_epoch(test_last, cfg["tz"]))
    qual = {metric: qualify(city, block, test_dates, metric, truth_all[metric], workers, step, cell, forecast)
            for metric in ("high", "low")}
    block["runtime_s"] = round(time.time() - t_start, 1)
    return block, qual


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cities", nargs="*", default=list(CITIES))
    ap.add_argument("--train-last", required=True, help="last training local date, YYYY-MM-DD")
    ap.add_argument("--test-last", required=True, help="last held-out local date for qualification, YYYY-MM-DD")
    ap.add_argument("--variants", type=int, default=8)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--step", type=float, default=30.0, help="qualification decision step, minutes")
    ap.add_argument("--cell", type=float, default=0.1)
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    reports, checks = lifecycle_inputs(args.train_last)
    lc = fit.fit_lifecycle(reports, checks)
    print("lifecycle", json.dumps({k: lc[k] for k in ("kept", "removed", "gross", "outage_prior", "visibility",
                                                       "n_reports", "n_outage_reports", "n_fetch_checks")}), flush=True)
    cities, quals = {}, {}
    for city in args.cities:
        cities[city], quals[city] = fit_city(city, args.train_last, args.test_last, lc, args.train_last, args.variants,
                                             args.workers, args.step, args.cell)
        print(city, cities[city]["selection"]["picked"], {m: q["eligible"] for m, q in quals[city].items()},
              cities[city]["runtime_s"], flush=True)
    report = dict(generated_at=datetime.now(UTC).isoformat(), train_last=args.train_last, test_last=args.test_last,
                  step_minutes=args.step, cell=args.cell, lifecycle_fit=lc,
                  law_hashes={c: block_hash(b) for c, b in cities.items()}, cities=quals)
    report = json.loads(json.dumps(report, default=_jsonable))
    report_text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    qualification_hash = hashlib.sha256(report_text.encode()).hexdigest()
    for city, q in quals.items():
        cities[city]["metrics"] = [m for m in ("high", "low") if q[m]["eligible"]]
        cities[city]["qualification"] = dict(report_sha256=qualification_hash,
                                             eligible={m: q[m]["eligible"] for m in ("high", "low")})
    payload = dict(schema_version=SCHEMA_VERSION, artifact=ARTIFACT_KIND, data_version="day0_dense_state_space_v2",
                   training_cutoff=args.train_last, qualification_hash=qualification_hash,
                   fitted_at=datetime.now(UTC).isoformat(), cities=cities)
    payload = json.loads(json.dumps(payload, default=_jsonable))
    payload["content_hash"] = canonical_hash(payload)
    _atomic_write(args.report, report_text)
    _atomic_write(args.out, json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print("wrote", args.out, payload["content_hash"], "report", args.report, qualification_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
